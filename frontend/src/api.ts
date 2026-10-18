let csrf = "";
export function requestKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // getRandomValues also works on an HTTP LAN origin, unlike randomUUID.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
export function setCsrf(value: string) {
  csrf = value;
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", csrf);
    headers.set("Idempotency-Key", requestKey());
  }
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.code || data.detail?.[0]?.msg || "REQUEST_FAILED");
  return data;
}
export const post = <T = any>(path: string, body: any = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });

export type Ticket = {
  id: string;
  document_id: string;
  date: string;
  number: number;
  label: string;
  state: string;
  companion: number;
  window: number | null;
  version: number;
  original_match: boolean;
  calls: number;
  call_attempts: number;
  last_called_at: string | null;
  call_wait_seconds: number;
  started_at: string | null;
  completed_at: string | null;
  episodes: Episode[];
};
export type Episode = {
  id: string;
  ticket_id: string;
  number: number;
  state: string;
  window: number | null;
  version: number;
  original_match: boolean;
  calls: number;
  call_attempts: number;
  last_called_at: string;
  call_wait_seconds: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};
export type Day = {
  id: string;
  date: string;
  free: number;
  held: number;
  allocated: number;
  consumed: number;
  capacity: number;
  closed: boolean;
  service_status: string;
};
export type WorkdaySummary = Day & {
  server_now: string;
  hours_enforced: boolean;
  recall_interval_seconds: number;
  counts: Record<string, number>;
  unresolved: Ticket[];
  in_service: Ticket[];
  repeat_unresolved: Episode[];
  repeat_in_service: Episode[];
  repeat_completed: number;
};
export type Fields = {
  country: string;
  number: string;
  name: string;
  birth_date: string;
  expiry_date: string;
};
export type Doc = { id: string; fields: Fields; verified: boolean; version: number; revision: number };
