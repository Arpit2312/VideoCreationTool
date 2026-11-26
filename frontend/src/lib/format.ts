export function timeAgo(iso?: string | null, now: number = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.round((now - t) / 1000);
  if (s < 0) return "just now";
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function fmtBytes(n?: number | null): string {
  if (n === undefined || n === null) return "";
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${u[i]}`;
}

export function fmtUsd(n?: number | null): string {
  if (!n) return "$0.00";
  return `$${n.toFixed(n < 1 ? 3 : 2)}`;
}

export function fmtDuration(sec?: number | null): string {
  if (sec === undefined || sec === null || Number.isNaN(sec)) return "—";
  const s = Math.round(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m ? `${m}:${String(r).padStart(2, "0")}` : `${r}s`;
}

/** Media kind from a path, for previews. */
export function mediaKind(path: string): "video" | "image" | "audio" | "text" | "json" | "other" {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  if (["mp4", "webm", "mov", "mkv", "m4v"].includes(ext)) return "video";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg", "avif"].includes(ext)) return "image";
  if (["wav", "mp3", "ogg", "m4a", "flac", "aac", "opus"].includes(ext)) return "audio";
  if (ext === "json" || ext === "jsonl") return "json";
  if (["md", "txt", "srt", "vtt", "log", "yaml", "yml", "csv"].includes(ext)) return "text";
  return "other";
}

export function isEditable(path: string): boolean {
  return /\.(json|md|txt|srt|vtt)$/i.test(path) && !/^logs\//.test(path);
}

/** Read a preset entry (string or object) into {id, name, description}. */
export function optionOf(x: unknown): { id: string; name: string; description?: string; raw: Record<string, unknown> } {
  if (typeof x === "string") return { id: x, name: x.replace(/[-_]/g, " "), raw: {} };
  const o = (x || {}) as Record<string, unknown>;
  const id = String(o.id ?? o.slug ?? o.value ?? o.name ?? "");
  const name = String(o.name ?? o.en ?? o.label ?? o.title ?? id);
  const description = (o.description ?? o.detail ?? o.desc) as string | undefined;
  return { id, name, description, raw: o };
}

/** Parse "1920x1080" → [1920,1080]. */
export function parseResolution(r?: string): [number, number] | null {
  const m = /^(\d+)\s*[x×]\s*(\d+)$/.exec(r || "");
  return m ? [Number(m[1]), Number(m[2])] : null;
}
