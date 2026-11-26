import { type ReactNode, useEffect, useRef } from "react";
import { AlertTriangle, CircleDollarSign, FlaskConical, Inbox, Loader2, RefreshCw, X } from "lucide-react";
import { Link } from "react-router-dom";
import { explainError } from "../api";
import { statusMeta, TONE_CHIP, TONE_SOLID } from "../lib/status";
import type { Stage } from "../types";
import { orderStages } from "../lib/timeline";

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(" ");
}

export function StatusBadge({ status, className }: { status: string | null | undefined; className?: string }) {
  const m = statusMeta(status);
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset whitespace-nowrap",
        TONE_CHIP[m.tone],
        className,
      )}
    >
      <span className={cx("h-1.5 w-1.5 rounded-full", TONE_SOLID[m.tone], m.live && "animate-softpulse")} />
      {m.label}
    </span>
  );
}

export function StatusDot({ status }: { status: string }) {
  const m = statusMeta(status);
  return <span title={m.label} className={cx("inline-block h-2 w-2 rounded-full", TONE_SOLID[m.tone], m.live && "animate-softpulse")} />;
}

export function Badge({ children, tone = "grey", title }: { children: ReactNode; tone?: keyof typeof TONE_CHIP; title?: string }) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset whitespace-nowrap",
        TONE_CHIP[tone],
      )}
    >
      {children}
    </span>
  );
}

export function SimulatedBadge() {
  return (
    <Badge tone="orange" title="The configured Claude binary is not the real `claude` CLI. Outputs are simulated test data.">
      <FlaskConical className="h-3 w-3" /> SIMULATED
    </Badge>
  );
}

export function ZeroCostBadge({ on = true }: { on?: boolean }) {
  return on ? (
    <Badge tone="green" title="Zero-cost mode: paid provider keys are stripped from every engine's environment.">
      <CircleDollarSign className="h-3 w-3" /> Zero-cost
    </Badge>
  ) : (
    <Badge tone="amber" title="Paid providers may be used for approved providers only.">
      <CircleDollarSign className="h-3 w-3" /> Paid allowed
    </Badge>
  );
}

export function EngineBadge({ owner }: { owner: string }) {
  if (!owner || owner === "spacestudio") return <Badge tone="slate">SpaceStudio</Badge>;
  return <Badge tone="blue">{owner}</Badge>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-muted text-sm" role="status">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  );
}

export function ErrorBox({ error, onRetry, title = "Something went wrong" }: { error: unknown; onRetry?: () => void; title?: string }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-700 dark:text-red-200 flex gap-2 items-start">
      <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="font-medium">{title}</div>
        <div className="break-words opacity-90">{explainError(error)}</div>
      </div>
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          <RefreshCw className="h-3 w-3" /> Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-8 text-center text-sm text-muted">
      {icon ?? <Inbox className="h-6 w-6 opacity-60" />}
      <div>{children}</div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
      <div className="min-w-0">
        <h1 className="h1">{title}</h1>
        {subtitle && <p className="text-sm text-muted mt-1">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Section({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("card p-4", className)}>
      {(title || actions) && (
        <div className="flex items-center justify-between gap-2 mb-3">
          {title && <h2 className="h2">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function ProgressBar({ value, tone = "blue" }: { value: number; tone?: keyof typeof TONE_SOLID }) {
  const pct = Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
  return (
    <div className="h-1.5 w-full rounded-full bg-panel2 overflow-hidden" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <div className={cx("h-full rounded-full transition-all", TONE_SOLID[tone])} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function SlotMeter({ label, used, max }: { label: string; used: number; max: number }) {
  const full = max > 0 && used >= max;
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-muted">{label}</span>
        <span className={cx("font-mono", full && "text-amber-600 dark:text-amber-300")}>
          {used}/{max}
        </span>
      </div>
      <div className="flex gap-1">
        {Array.from({ length: Math.max(max, 1) }).map((_, i) => (
          <div key={i} className={cx("h-2 flex-1 rounded-sm", i < used ? "bg-blue-500" : "bg-panel2 border border-line")} />
        ))}
      </div>
    </div>
  );
}

/** Compact strip of stage statuses (one segment per stage). */
export function StageStrip({ stages, videoId }: { stages: Stage[]; videoId?: string }) {
  const ordered = orderStages(stages);
  if (!ordered.length) return <span className="subtle">No stages planned yet</span>;
  return (
    <div className="flex gap-0.5 w-full" aria-label="Stage progress">
      {ordered.map((s) => {
        const m = statusMeta(s.status);
        const el = (
          <div
            title={`${s.name}: ${m.label}`}
            className={cx("h-2 flex-1 min-w-[6px] rounded-sm", TONE_SOLID[m.tone], m.live && "animate-softpulse")}
          />
        );
        return videoId ? (
          <Link key={s.key} to={`/videos/${videoId}?stage=${encodeURIComponent(s.key)}`} className="flex-1 min-w-[6px] flex">
            {el}
          </Link>
        ) : (
          <div key={s.key} className="flex-1 min-w-[6px] flex">
            {el}
          </div>
        );
      })}
    </div>
  );
}

export function Modal({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60 p-0 sm:p-4" onClick={onClose}>
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        className="card w-full sm:max-w-lg max-h-[90vh] overflow-auto p-5 shadow-2xl outline-none rounded-b-none sm:rounded-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h2 className="h2">{title}</h2>
          <button className="btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="text-sm">{children}</div>
        {footer && <div className="flex justify-end gap-2 mt-4">{footer}</div>}
      </div>
    </div>
  );
}

export function Toggle({ checked, onChange, label, description, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; description?: ReactNode; disabled?: boolean }) {
  return (
    <label className={cx("flex items-start gap-3 cursor-pointer select-none", disabled && "opacity-50 cursor-not-allowed")}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx("mt-0.5 relative h-5 w-9 shrink-0 rounded-full transition", checked ? "bg-accent" : "bg-zinc-400 dark:bg-zinc-600")}
      >
        <span className={cx("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all", checked ? "left-[18px]" : "left-0.5")} />
      </button>
      <span className="text-sm">
        <span className="font-medium">{label}</span>
        {description && <span className="block text-xs text-muted mt-0.5">{description}</span>}
      </span>
    </label>
  );
}

export function Field({ label, children, hint, className }: { label: ReactNode; children: ReactNode; hint?: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <label className="label">{label}</label>
      {children}
      {hint && <p className="text-[11px] text-muted mt-1">{hint}</p>}
    </div>
  );
}

export function Select({ value, onChange, options, className, ariaLabel }: { value: string; onChange: (v: string) => void; options: { id: string; name: string }[]; className?: string; ariaLabel?: string }) {
  const has = options.some((o) => o.id === value);
  return (
    <select aria-label={ariaLabel} className={cx("input", className)} value={value} onChange={(e) => onChange(e.target.value)}>
      {!has && value !== "" && <option value={value}>{value}</option>}
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
    </select>
  );
}

/** Comma-separated list input. */
export function ListInput({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  return (
    <input
      className="input"
      placeholder={placeholder}
      defaultValue={(value || []).join(", ")}
      onBlur={(e) =>
        onChange(
          e.target.value
            .split(",")
            .map((x) => x.trim())
            .filter(Boolean),
        )
      }
    />
  );
}

export function Stat({ label, value, tone, to, icon }: { label: string; value: ReactNode; tone?: keyof typeof TONE_SOLID; to?: string; icon?: ReactNode }) {
  const body = (
    <div className="card p-4 h-full hover:border-muted/40 transition">
      <div className="flex items-center gap-2 text-xs text-muted">
        {tone && <span className={cx("h-2 w-2 rounded-full", TONE_SOLID[tone], tone === "blue" && "animate-softpulse")} />}
        {icon}
        {label}
      </div>
      <div className="text-2xl font-semibold mt-1 tabular-nums">{value}</div>
    </div>
  );
  return to ? <Link to={to}>{body}</Link> : body;
}

export function KV({ k, children }: { k: ReactNode; children: ReactNode }) {
  return (
    <div className="flex gap-2 text-xs py-0.5">
      <span className="text-muted w-28 shrink-0">{k}</span>
      <span className="min-w-0 break-words flex-1">{children}</span>
    </div>
  );
}
