import { describe, expect, it } from "vitest";
import { dateLabel, progress, states } from "./format";
describe("queue presentation", () => {
  it("does not turn a date into an appointment time", () => {
    expect(dateLabel("2026-10-15")).toContain("15");
  });
  it("clamps capacity display", () => {
    expect(progress(150)).toBe(100);
    expect(progress(-1)).toBe(0);
  });
  it("distinguishes skipped and no-show", () => {
    expect(states.SKIPPED).not.toBe(states.NO_SHOW);
  });
});
