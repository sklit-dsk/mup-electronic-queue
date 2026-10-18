export type Portal = "applicant" | "staff" | "admin" | "demo";

export function portalFromPath(pathname: string): Portal {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/staff") return "staff";
  if (path === "/admin") return "admin";
  if (path === "/demo") return "demo";
  return "applicant";
}

export function isRoleAllowed(portal: Exclude<Portal, "demo">, role: string) {
  return (portal === "applicant" && role === "applicant") ||
    (portal === "staff" && ["staff", "admin"].includes(role)) ||
    (portal === "admin" && role === "admin");
}
