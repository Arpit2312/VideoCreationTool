import { describe, expect, it } from "vitest";
import { allowedStageActions, bucketOf, stageCounts, statusMeta } from "./status";

describe("statusMeta", () => {
  it("maps every spec status to the agreed tone", () => {
    expect(statusMeta("pending").tone).toBe("grey");
    expect(statusMeta("queued").tone).toBe("slate");
    expect(statusMeta("running").tone).toBe("blue");
    expect(statusMeta("running").live).toBe(true);
    expect(statusMeta("awaiting_approval").tone).toBe("amber");
    expect(statusMeta("needs_input").tone).toBe("violet");
    expect(statusMeta("needs_input").label).toBe("Needs your input");
    expect(statusMeta("completed").tone).toBe("green");
    expect(statusMeta("failed").tone).toBe("red");
    expect(statusMeta("cancelled").tone).toBe("muted");
    expect(statusMeta("skipped").tone).toBe("muted");
  });
  it("handles unknown and empty values", () => {
    expect(statusMeta("weird_state").label).toBe("Weird state");
    expect(statusMeta(undefined).label).toBe("Unknown");
  });
});

describe("bucketOf", () => {
  it("buckets job statuses for the dashboard", () => {
    expect(bucketOf("running")).toBe("running");
    expect(bucketOf("awaiting_approval")).toBe("approval");
    expect(bucketOf("needs_input")).toBe("input");
    expect(bucketOf("interrupted")).toBe("failed");
    expect(bucketOf("completed")).toBe("completed");
    expect(bucketOf("draft")).toBe("other");
  });
});

describe("allowedStageActions", () => {
  it("only offers skip for non-required stages", () => {
    expect(allowedStageActions({ status: "failed", required: true }).skip).toBe(false);
    expect(allowedStageActions({ status: "failed", required: false }).skip).toBe(true);
    expect(allowedStageActions({ status: "running", required: false }).skip).toBe(false);
  });
  it("gates approve/reject on awaiting_approval and answer on needs_input", () => {
    const a = allowedStageActions({ status: "awaiting_approval", required: true });
    expect(a.approve && a.reject && a.requestChanges).toBe(true);
    expect(a.retry).toBe(false);
    expect(allowedStageActions({ status: "needs_input", required: true }).answer).toBe(true);
    expect(allowedStageActions({ status: "failed", required: true }).retry).toBe(true);
  });
});

describe("stageCounts", () => {
  it("counts done as completed + skipped", () => {
    const c = stageCounts([{ status: "completed" }, { status: "skipped" }, { status: "running" }]);
    expect(c.total).toBe(3);
    expect(c.done).toBe(2);
    expect(c.running).toBe(1);
  });
});
