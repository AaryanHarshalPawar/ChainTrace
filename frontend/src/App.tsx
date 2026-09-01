/**
 * ChainTrace investigator workbench.
 *
 * Two views over one trace. The **report** is the deliverable an officer files
 * and acts on, so it opens first; the **graph** is the exploratory view for
 * following the money by eye. Both render the same result object -- there is
 * no second source of truth.
 */

import { useEffect, useMemo, useState } from "react";
import { ApiError, api, type HealthResponse, type TraceResult } from "./api/client";
import { FlowGraph } from "./components/FlowGraph";
import { Report } from "./components/Report";
import { NodeInspector } from "./components/Panels";

// Chosen by tracing candidates and keeping the ones that actually produce a
// finding worth reading. Each shows a different outcome, so a demo can move
// between them without repeating itself. All are pre-cached, so they return in
// well under a second and work with the network unplugged.
const EXAMPLES = [
  {
    label: "Sanctions hit",
    address: "32pTjxTNi7snk8sodrgfmdKao3DEn1nVJM",
    note: "CRITICAL · OFAC-designated individual, $4.12m from 27 payers",
  },
  {
    label: "Layered · 52 edges",
    address: "37cGxZ3EcZ7JSyTwzBbmw5JJCRkKm1ysea",
    note: "HIGH · exchange reached at hop 2, taint diluted to 8.4%",
  },
  {
    label: "Deposit address found",
    address: "bc1qgvzl4wklayt4dnmzugcwcern5ceyg4h5j4tm83",
    note: "MEDIUM · sweep into a VASP at hop 1",
  },
  {
    label: "TRON · single chain",
    address: "TWd4WrZ9wn84f5x1hZhL4DHvk738ns5jwb",
    note: "98.7% taint carried undiluted to an exchange wallet",
  },
];

type View = "report" | "graph";

/** Deterministic case reference, so the same wallet always files under the
 *  same number across a demo rather than changing on every re-run. */
function caseIdFor(address: string, when: Date): string {
  let hash = 0;
  for (const ch of address) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return `TRC-${when.getFullYear()}-${String(hash % 10000).padStart(4, "0")}`;
}

export default function App() {
  const [address, setAddress] = useState("");
  const [result, setResult] = useState<TraceResult | null>(null);
  const [view, setView] = useState<View>("report");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [maxHops, setMaxHops] = useState(3);

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
      const response = await api.trace(value, { maxHops, maxNodes: 40 });
      setResult(response.result);
      setSelected(response.result.nodes?.[0]?.address ?? null);
      setView("report");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const caseId = useMemo(
    () =>
      result
        ? caseIdFor(result.subject_address, new Date(result.generated_at))
        : "",
    [result],
  );
  const selectedNode = result?.nodes?.find((n) => n.address === selected) ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100%" }}>
      {/* ---------------- header ---------------- */}
      <header
        className="no-print"
        style={{
          background: "var(--surface)",
          borderBottom: "1px solid var(--line)",
          padding: "11px 20px",
          display: "flex",
          alignItems: "center",
          gap: 16,
          flexWrap: "wrap",
          position: "sticky",
          top: 0,
          zIndex: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 9 }}>
          <span
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: 18,
              fontWeight: 700,
              letterSpacing: "-0.015em",
            }}
          >
            ChainTrace
          </span>
          <span className="label" style={{ fontSize: 9.5 }}>
            LEA wallet attribution
          </span>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            runTrace(address);
          }}
          style={{ display: "flex", gap: 7, flex: 1, minWidth: 300, maxWidth: 560 }}
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
              fontSize: 12,
              border: "1px solid var(--line-strong)",
              borderRadius: 6,
              background: "var(--bg)",
              color: "var(--ink)",
            }}
          />
          <select
            value={maxHops}
            onChange={(e) => setMaxHops(Number(e.target.value))}
            title="How many hops to follow"
            style={{
              padding: "8px 6px",
              fontSize: 12,
              border: "1px solid var(--line-strong)",
              borderRadius: 6,
              background: "var(--bg)",
              color: "var(--ink)",
            }}
          >
            {[2, 3, 4, 5].map((h) => (
              <option key={h} value={h}>
                {h} hops
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={busy || !address.trim()}
            style={{
              padding: "8px 18px",
              fontSize: 12.5,
              fontWeight: 500,
              color: busy || !address.trim() ? "var(--muted)" : "#08181a",
              background:
                busy || !address.trim() ? "var(--surface-3)" : "var(--accent)",
              border: 0,
              borderRadius: 6,
            }}
          >
            {busy ? "Tracing…" : "Trace"}
          </button>
        </form>

        <span
          className="mono"
          style={{ fontSize: 10, color: "var(--muted)", marginLeft: "auto" }}
        >
          {health
            ? `${health.labels.total_addresses} labels · ${health.supported_chains.join(", ")}`
            : "server offline"}
        </span>
      </header>

      {/* Examples live on their own strip rather than in the header: three
          buttons plus the search form overflow on a narrow projector, and a
          demo is not the moment to discover that. */}
      <div
        className="no-print"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          padding: "9px 20px",
          borderBottom: "1px solid var(--line)",
          background: "var(--surface-2)",
        }}
      >
        <span className="label">Try</span>
        {EXAMPLES.map((ex) => (
          <button
            key={ex.address}
            onClick={() => {
              setAddress(ex.address);
              runTrace(ex.address);
            }}
            disabled={busy}
            title={ex.note}
            style={{
              padding: "5px 11px",
              fontSize: 11.5,
              background: "transparent",
              border: "1px solid var(--line-strong)",
              borderRadius: 5,
              color: "var(--ink-2)",
            }}
          >
            {ex.label}
          </button>
        ))}
      </div>

      {/* ---------------- view switch ---------------- */}
      {result && (
        <div
          className="no-print"
          style={{
            display: "flex",
            gap: 6,
            padding: "10px 20px 0",
            alignItems: "center",
          }}
        >
          {(["report", "graph"] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              style={{
                padding: "6px 16px",
                fontSize: 12,
                textTransform: "capitalize",
                background: view === v ? "var(--surface)" : "transparent",
                border: `1px solid ${view === v ? "var(--accent)" : "var(--line)"}`,
                color: view === v ? "var(--accent-ink)" : "var(--muted)",
                borderRadius: 6,
              }}
            >
              {v === "report" ? "Investigation report" : "Fund-flow graph"}
            </button>
          ))}
          <button
            onClick={() => window.print()}
            // Windows defaults some machines to the XPS Document Writer, which
            // silently saves .oxps instead of a PDF and looks like a broken
            // export. Naming the destination on the control is cheaper than
            // discovering it mid-presentation.
            title={
              "In the print dialog set Destination to 'Save as PDF' " +
              "(not 'Microsoft XPS Document Writer'), and turn on " +
              "'Background graphics' so the risk banners print."
            }
            style={{
              marginLeft: "auto",
              padding: "6px 14px",
              fontSize: 12,
              background: "transparent",
              border: "1px solid var(--line-strong)",
              borderRadius: 6,
              color: "var(--ink-2)",
            }}
          >
            Save as PDF
          </button>
        </div>
      )}

      {/* ---------------- body ---------------- */}
      <main style={{ flex: 1, minHeight: 0, padding: result ? "16px 20px" : 0 }}>
        {!result ? (
          <div
            className="no-print"
            style={{
              height: "70vh",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 12,
              textAlign: "center",
              padding: 24,
            }}
          >
            {busy ? (
              <>
                <div style={{ fontSize: 16, color: "var(--ink-2)" }}>
                  Following the money…
                </div>
                <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                  reading the chain hop by hop
                </div>
              </>
            ) : error ? (
              <div
                style={{
                  maxWidth: 480,
                  background: "var(--critical-soft)",
                  border: "1px solid var(--critical)",
                  borderRadius: 6,
                  padding: "14px 18px",
                  color: "var(--critical)",
                  fontSize: 13,
                }}
              >
                {error}
              </div>
            ) : (
              <>
                <div
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontSize: 22,
                    fontWeight: 600,
                  }}
                >
                  Paste a reported wallet address
                </div>
                <p
                  style={{
                    maxWidth: 440,
                    fontSize: 13,
                    color: "var(--muted)",
                    margin: 0,
                  }}
                >
                  ChainTrace follows the funds across the blockchain, identifies
                  the exchange that received them, and produces a filed report
                  with the evidence for every claim.
                </p>
              </>
            )}
          </div>
        ) : view === "report" ? (
          <Report result={result} caseId={caseId} />
        ) : (
          <div style={{ display: "flex", gap: 14, height: "calc(100vh - 150px)" }}>
            <div
              className="card"
              style={{ flex: 1, minWidth: 0, overflow: "hidden" }}
            >
              <FlowGraph
                nodes={result.nodes ?? []}
                edges={result.edges ?? []}
                selectedAddress={selected}
                onSelect={setSelected}
              />
            </div>
            {selectedNode && (
              <aside style={{ width: 330, flexShrink: 0, overflowY: "auto" }}>
                <NodeInspector node={selectedNode} />
              </aside>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
