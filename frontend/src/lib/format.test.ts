import { describe, expect, it } from "vitest";
import { fmtBytes, mediaKind, optionOf, parseResolution, timeAgo } from "./format";

describe("format helpers", () => {
  it("timeAgo", () => {
    const now = Date.parse("2026-01-01T00:10:00Z");
    expect(timeAgo("2026-01-01T00:09:30Z", now)).toBe("30s ago");
    expect(timeAgo("2026-01-01T00:00:00Z", now)).toBe("10m ago");
    expect(timeAgo(null, now)).toBe("—");
  });
  it("fmtBytes", () => {
    expect(fmtBytes(500)).toBe("500 B");
    expect(fmtBytes(2048)).toBe("2.0 KB");
  });
  it("mediaKind", () => {
    expect(mediaKind("renders/final.mp4")).toBe("video");
    expect(mediaKind("a/b.PNG")).toBe("image");
    expect(mediaKind("narration/narration.wav")).toBe("audio");
    expect(mediaKind("scripts/script.json")).toBe("json");
  });
  it("optionOf reads strings and lemo style objects", () => {
    expect(optionOf("cinematic").id).toBe("cinematic");
    const o = optionOf({ slug: "whiteboard", en: "Whiteboard", category_en: "Hand-drawn" });
    expect(o.id).toBe("whiteboard");
    expect(o.name).toBe("Whiteboard");
  });
  it("parseResolution", () => {
    expect(parseResolution("1920x1080")).toEqual([1920, 1080]);
    expect(parseResolution("bad")).toBeNull();
  });
});
