import { describe, expect, it } from "vitest";
import { isRoleAllowed, portalFromPath } from "./portal";

describe("portal separation", () => {
  it("maps public routes to isolated portals", () => {
    expect(portalFromPath("/")).toBe("applicant");
    expect(portalFromPath("/staff/")).toBe("staff");
    expect(portalFromPath("/admin")).toBe("admin");
    expect(portalFromPath("/demo")).toBe("demo");
  });

  it("does not expose privileged portals to lower roles", () => {
    expect(isRoleAllowed("applicant", "applicant")).toBe(true);
    expect(isRoleAllowed("applicant", "staff")).toBe(false);
    expect(isRoleAllowed("staff", "staff")).toBe(true);
    expect(isRoleAllowed("staff", "admin")).toBe(true);
    expect(isRoleAllowed("staff", "applicant")).toBe(false);
    expect(isRoleAllowed("admin", "admin")).toBe(true);
    expect(isRoleAllowed("admin", "staff")).toBe(false);
  });
});
