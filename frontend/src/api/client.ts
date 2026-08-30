/**
 * Typed client for the ChainTrace API.
 *
 * Every type here comes from `schema.d.ts`, which is generated from the live
 * FastAPI schema (`npm run gen:api`). Nothing is hand-typed, so renaming a
 * field in Python breaks the build here rather than silently rendering
 * `undefined` in front of an investigator.
 */

import type { components } from "./schema";

export type TraceResult = components["schemas"]["TraceResult"];
export type TraceNode = components["schemas"]["TraceNode"];
export type TraceEdge = components["schemas"]["TraceEdge"];
export type Attribution = components["schemas"]["Attribution"];
export type RiskAssessment = components["schemas"]["RiskAssessment"];
export type RiskSignal = components["schemas"]["RiskSignal"];
export type InvestigativeAction = components["schemas"]["InvestigativeAction"];
export type AddressProfile = components["schemas"]["AddressProfile"];
export type ValidationResponse = components["schemas"]["ValidationResponse"];

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api/v1";

/** An API failure with a message worth showing the user. */
export class ApiError extends Error {
  // Declared and assigned separately rather than as a constructor parameter
  // property: this project builds with `erasableSyntaxOnly`, which forbids
  // TypeScript syntax that emits runtime code.
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // fetch only rejects on network-level failure, and by far the most likely
    // cause during development is that the API simply is not running.
    throw new ApiError(
      "Cannot reach the ChainTrace server. Start it in VS Code with F5, then try again.",
      0,
    );
  }

  if (!response.ok) {
    // FastAPI puts the useful message in `detail`; surface it rather than a
    // bare status code, since 422 here means "this address is malformed and
    // here is exactly why".
    let detail = `Request failed (HTTP ${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg)
        detail = body.detail[0].msg;
    } catch {
      /* response had no JSON body; the status-based message stands */
    }
    throw new ApiError(detail, response.status);
  }

  return response.json() as Promise<T>;
}

export interface HealthResponse {
  status: string;
  offline_mode: boolean;
  live_prices: boolean;
  supported_chains: string[];
  labels: {
    total_addresses: number;
    ofac_entries: number;
    ofac_publish_date: string | null;
  };
  cache: { entries: number };
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  validate: (address: string) =>
    request<ValidationResponse>(
      `/validate?address=${encodeURIComponent(address)}`,
    ),

  screen: (address: string) =>
    request<{
      address: string;
      is_sanctioned: boolean;
      hits: Array<{ name: string; source: string; category: string; notes?: string | null }>;
    }>(`/screen?address=${encodeURIComponent(address)}`),

  trace: (address: string, opts?: { maxHops?: number; maxNodes?: number; complaintId?: string }) =>
    request<{ complaint_id: string | null; result: TraceResult }>("/trace", {
      method: "POST",
      body: JSON.stringify({
        address,
        max_hops: opts?.maxHops ?? 3,
        max_nodes: opts?.maxNodes ?? 40,
        complaint_id: opts?.complaintId ?? null,
      }),
    }),
};
