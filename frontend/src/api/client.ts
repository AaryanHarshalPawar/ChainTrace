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

/** One live event from a streaming trace. */
export interface ProgressEvent {
  type: "stage" | "hop" | "node" | "done" | "error";
  /** stage */
  stage?: string;
  message?: string;
  chain?: string;
  /** hop + node */
  depth?: number;
  addresses?: number;
  /** node */
  address?: string;
  role?: string;
  label?: string | null;
  terminal?: boolean;
  /** done */
  result?: TraceResult;
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

  /**
   * Trace with live progress.
   *
   * Reads server-sent events off the response body. Every event originates in
   * the tracer as it works, so what the UI shows is the real state of the
   * search rather than a timed animation.
   */
  traceStream: async (
    address: string,
    opts: { maxHops?: number; maxNodes?: number; complaintId?: string },
    onEvent: (event: ProgressEvent) => void,
  ): Promise<TraceResult> => {
    let response: Response;
    try {
      response = await fetch(`${BASE}/trace/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          address,
          max_hops: opts.maxHops ?? 3,
          max_nodes: opts.maxNodes ?? 40,
          complaint_id: opts.complaintId ?? null,
        }),
      });
    } catch {
      throw new ApiError(
        "Cannot reach the ChainTrace server. Start it in VS Code with F5, then try again.",
        0,
      );
    }
    if (!response.ok || !response.body) {
      throw new ApiError(`Request failed (HTTP ${response.status})`, response.status);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: TraceResult | null = null;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line; the last piece may be partial.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        const line = frame.trim();
        if (!line.startsWith("data:")) continue;
        let event: ProgressEvent;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch {
          continue; // a malformed frame must not kill the whole trace
        }
        if (event.type === "error") {
          throw new ApiError(event.message ?? "Trace failed", 500);
        }
        if (event.type === "done" && event.result) {
          result = event.result;
        }
        onEvent(event);
      }
    }

    if (!result) {
      throw new ApiError("The trace ended without returning a result.", 500);
    }
    return result;
  },

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
