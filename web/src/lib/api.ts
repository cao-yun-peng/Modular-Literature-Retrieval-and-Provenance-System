import type { components } from "./schema";
export type Document = components["schemas"]["DocumentOut"];
export type Run = components["schemas"]["RunOut"];
export type Chunk = components["schemas"]["ChunkOut"];
export type Evidence = components["schemas"]["EvidenceOut"];
export type Event = components["schemas"]["RunEvent"];
export type Config = components["schemas"]["PublicConfig"];
export type Result = components["schemas"]["RetrievalResultOut"];
export type Page<T> = { items: T[]; next_cursor: string | null };
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public retryable = false,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api/v1" + path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      body.error?.code ?? "network",
      body.error?.message ?? `请求失败 (${response.status})`,
      body.error?.retryable,
    );
  }
  return response.json();
}
export function post<T>(
  path: string,
  body: unknown,
  key = crypto.randomUUID(),
) {
  return api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": key },
    body: JSON.stringify(body),
  });
}
export function params(values: Record<string, unknown>) {
  const p = new URLSearchParams();
  Object.entries(values).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  });
  return p.toString();
}
export const terminal = (status?: string) =>
  ["succeeded", "failed", "interrupted"].includes(status ?? "");
