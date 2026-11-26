// SSE subscription: invalidates react-query caches on backend events.
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { API_BASE } from "../api";

export type Conn = "connecting" | "open" | "error";

type Payload = { video_id?: string; stage_key?: string; key?: string; batch_id?: string; [k: string]: unknown };

function parse(e: MessageEvent): Payload {
  try {
    return JSON.parse(e.data) as Payload;
  } catch {
    return {};
  }
}

export function useServerEvents(): Conn {
  const qc = useQueryClient();
  const [conn, setConn] = useState<Conn>("connecting");
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    const connect = () => {
      es = new EventSource(`${API_BASE}/events`);
      es.onopen = () => setConn("open");
      es.onerror = () => {
        setConn("error");
        es?.close();
        if (!closed) timer = setTimeout(connect, 4000);
      };
      const onVideo = (e: MessageEvent) => {
        const p = parse(e);
        qc.invalidateQueries({ queryKey: ["videos"] });
        qc.invalidateQueries({ queryKey: ["batches"] });
        qc.invalidateQueries({ queryKey: ["batch"] });
        if (p.video_id) {
          qc.invalidateQueries({ queryKey: ["video", p.video_id] });
          qc.invalidateQueries({ queryKey: ["history", p.video_id] });
        } else qc.invalidateQueries({ queryKey: ["video"] });
      };
      const onStage = (e: MessageEvent) => {
        const p = parse(e);
        onVideo(e);
        if (p.video_id) {
          qc.invalidateQueries({ queryKey: ["stage", p.video_id] });
          qc.invalidateQueries({ queryKey: ["files", p.video_id] });
          qc.invalidateQueries({ queryKey: ["vfile", p.video_id] });
          qc.invalidateQueries({ queryKey: ["assets", p.video_id] });
        } else qc.invalidateQueries({ queryKey: ["stage"] });
      };
      es.addEventListener("video.updated", onVideo);
      es.addEventListener("stage.updated", onStage);
      es.addEventListener("log", (e) => {
        const p = parse(e as MessageEvent);
        if (p.video_id) qc.invalidateQueries({ queryKey: ["stage", p.video_id, p.stage_key ?? p.key] });
      });
      es.addEventListener("queue.updated", () => qc.invalidateQueries({ queryKey: ["queue"] }));
      es.addEventListener("health.updated", () => {
        qc.invalidateQueries({ queryKey: ["health"] });
        qc.invalidateQueries({ queryKey: ["engines"] });
      });
    };
    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      es?.close();
    };
  }, [qc]);
  return conn;
}
