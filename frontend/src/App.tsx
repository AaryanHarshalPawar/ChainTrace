/**
 * ChainTrace investigator workbench.
 *
 * One screen, one job: an officer pastes a wallet address from a complaint and
 * gets back where the money went, who to contact, and what to do about it.
 */

import { useEffect, useState } from "react";
import { ApiError, api, type HealthResponse, type TraceResult } from "./api/client";
import { FlowGraph } from "./components/FlowGraph";
import {
  ActionsCard,
  AttributionCard,
  NodeInspector,
  RiskBanner,
  ScopeCard,
  SignalsCard,
} from "./components/Panels";

const DEMO = "TMuA6YqfCeX8EhbfYEg5y7S4DqzSJireY9";

export default function App() {
  const [address, setAddress] = useState("");
  const [result, setResult] = useState<TraceResult | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  async function runTrace(target: string) {
    const value = target.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setSelected(null);
    try {
      const response = await api.trace(value, { complaintId: "NCRP-DEMO" });
      setResult(response.result);
      setSelected(response.result.nodes?.[0]?.address ?? null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const selectedNode = result?.nodes?.find((n) => n.address === selected) ?? null;
  const primary = result?.attributions?.[0] ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* ---- header ---- */}
      <header
        style={{
          background: "var(--surface)",
          borderBottom: "1px solid var(--line)",
          padding: "12px 20px",
          display: "flex",
          alignItems: "center",
          gap: 20,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <span
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: 19,
              fontWeight: 700,
              letterSpacing: "-0.015em",
            }}
          >
            ChainTrace
          </span>
          <span className="label">Wallet attribution for LEA</span>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            runTrace(address);
          }}
          style={{ display: "flex", gap: 8, flex: 1, minWidth: 320, maxWidth: 620 }}
        >
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="Paste the wallet address from the complaint…"
            spellCheck={false}
            className="mono"
            style={{
              flex: 1,
              padding: "8px 12px",
              fontSize: 12.5,
              border: "1px solid var(--line-strong)",
              borderRadius: "var(--radius)",
              background: "var(--bg)",
              color: "var(--ink)",
            }}
          />
          <button
            type="submit"
            disabled={busy || !address.trim()}
            style={{
              padding: "8px 18px",
              fontSize: 13,
              fontWeight: 500,
              color: "#fff",
              background: busy || !address.trim() ? "var(--line-strong)" : "var(--accent)",
              border: 0,
              borderRadius: "var(--radius)",
            }}
          >
            {busy ? "Tracing…" : "Trace"}
          </button>
        </form>

        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 14 }}>
          <button
            onClick={() => {
              setAddress(DEMO);
              runTrace(DEMO);
            }}
            disabled={busy}
            style={{
              padding: "7px 12px",
              fontSize: 12,
              background: "transparent",
              border: "1px solid var(--line-strong)",
              borderRadius: "var(--radius)",
              color: "var(--ink-2)",
            }}
          >
            Load example
          </button>
          <span className="mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>
            {health
              ? `${health.labels.total_addresses} labels · ${health.supported_chains.join(", ")}`
              : "server offline"}
          </span>
        </div>
      </header>

      {/* ---- body ---- */}
      <main style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <section style={{ flex: 1, position: "relative", minWidth: 0 }}>
          {result && result.nodes?.length ? (
            <FlowGraph
              nodes={result.nodes}
              edges={result.edges ?? []}
              selectedAddress={selected}
              onSelect={setSelected}
            />
          ) : (
            <div
              style={{
                height: "100%",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 10,
                padding: 24,
                textAlign: "center",
              }}
            >
              {busy ? (
                <>
                  <div style={{ fontSize: 15, color: "var(--ink-2)" }}>
                    Following the money…
                  </div>
                  <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                    reading the chain hop by hop
                  </div>
                </>
              ) : error ? (
                <div
                  style={{
                    maxWidth: 460,
                    background: "var(--critical-soft)",
                    border: "1px solid var(--critical)",
                    borderRadius: "var(--radius)",
                    padding: "14px 18px",
                    color: "var(--critical)",
                    fontSize: 13,
                  }}
                >
                  {error}
                </div>
              ) : (
                <>
                  <div style={{ fontFamily: "var(--font-serif)", fontSize: 20, fontWeight: 600 }}>
                    Paste a reported wallet address
                  </div>
                  <p style={{ maxWidth: 420, fontSize: 13, color: "var(--muted)", margin: 0 }}>
                    ChainTrace follows the funds across the blockchain and
                    identifies the nearest exchange that received them, with the
                    evidence for every claim.
                  </p>
                </>
              )}
            </div>
          )}
        </section>

        {result && (
          <aside
            style={{
              width: 380,
              flexShrink: 0,
              borderLeft: "1px solid var(--line)",
              background: "var(--bg)",
              overflowY: "auto",
              padding: 16,
              display: "flex",
              flexDirection: "column",
              gap: 14,
            }}
          >
            {result.risk && (
              <RiskBanner risk={result.risk} elapsed={result.stats?.elapsed_seconds ?? 0} />
            )}
            {primary && <AttributionCard attribution={primary} />}
            <ActionsCard actions={result.recommended_actions ?? []} />
            {selectedNode && <NodeInspector node={selectedNode} />}
            {result.risk && <SignalsCard risk={result.risk} />}
            <ScopeCard
              stats={(result.stats ?? {}) as Record<string, unknown>}
              filtered={(result.filtered_summary ?? {}) as Record<string, number>}
            />
            {(result.warnings?.length ?? 0) > 0 && (
              <div style={{ fontSize: 11.5, color: "var(--high)" }}>
                {result.warnings?.map((w, i) => (
                  <p key={i} style={{ margin: "0 0 6px" }}>
                    ⚠ {w}
                  </p>
                ))}
              </div>
            )}
          </aside>
        )}
      </main>
    </div>
  );
}
