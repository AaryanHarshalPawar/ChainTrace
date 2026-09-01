/**
 * Live progress while a trace runs.
 *
 * A cold Bitcoin trace takes tens of seconds. A spinner during that tells the
 * investigator nothing -- not whether the search is advancing, how wide it has
 * become, or whether it has hung on a rate limit. Every line here is emitted
 * by the tracer as it actually works, so this is a view of the search, not an
 * animation timed to look busy.
 *
 * The fan-out count is the most informative number on screen: "hop 2 — 44
 * addresses queued" is the moment it becomes obvious why the search is
 * budgeted at all.
 */

import { useEffect, useRef } from "react";
import type { ProgressEvent } from "../api/client";
import { shortAddress } from "../format";

interface Props {
  events: ProgressEvent[];
  address: string;
}

function roleColour(role?: string): string {
  switch (role) {
    case "subject":
      return "var(--accent)";
    case "vasp_deposit":
      return "var(--critical)";
    case "vasp_hot":
      return "var(--high)";
    case "mixer":
      return "var(--critical)";
    default:
      return "var(--muted)";
  }
}

export function TraceProgress({ events, address }: Props) {
  const logRef = useRef<HTMLDivElement>(null);

  // Keep the newest line in view without yanking the whole page.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  const hops = events.filter((e) => e.type === "hop");
  const nodes = events.filter((e) => e.type === "node");
  const currentHop = hops.length ? (hops[hops.length - 1].depth ?? 0) : 0;
  const queued = hops.length ? (hops[hops.length - 1].addresses ?? 0) : 0;
  const lastStage = [...events].reverse().find((e) => e.type === "stage");

  return (
    <div
      style={{
        maxWidth: 620,
        margin: "0 auto",
        width: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <div style={{ textAlign: "center" }}>
        <div
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: 20,
            fontWeight: 600,
            marginBottom: 4,
          }}
        >
          Following the money
        </div>
        <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
          {shortAddress(address, 14, 8)}
        </div>
      </div>

      {/* counters */}
      <div style={{ display: "flex", gap: 10 }}>
        {[
          { k: "Current hop", v: String(currentHop) },
          { k: "Addresses examined", v: String(nodes.length) },
          { k: "Queued at this hop", v: String(queued) },
        ].map((t) => (
          <div
            key={t.k}
            style={{
              flex: 1,
              background: "var(--surface)",
              border: "1px solid var(--line)",
              borderRadius: 6,
              padding: "10px 12px",
              textAlign: "center",
            }}
          >
            <div className="label" style={{ fontSize: 9 }}>
              {t.k}
            </div>
            <div
              className="mono"
              style={{ fontSize: 20, fontWeight: 600, marginTop: 2 }}
            >
              {t.v}
            </div>
          </div>
        ))}
      </div>

      {lastStage && (
        <div
          style={{
            textAlign: "center",
            fontSize: 12.5,
            color: "var(--accent-ink)",
          }}
        >
          {lastStage.message}
        </div>
      )}

      {/* live log */}
      <div
        ref={logRef}
        style={{
          height: 210,
          overflowY: "auto",
          background: "var(--surface)",
          border: "1px solid var(--line)",
          borderRadius: 6,
          padding: "10px 14px",
          fontFamily: "var(--font-mono)",
          fontSize: 11.5,
          lineHeight: 1.75,
        }}
      >
        {events.length === 0 && (
          <div style={{ color: "var(--muted)" }}>starting…</div>
        )}
        {events.map((e, i) => {
          if (e.type === "stage") {
            return (
              <div key={i} style={{ color: "var(--accent)" }}>
                › {e.message}
              </div>
            );
          }
          if (e.type === "hop") {
            return (
              <div
                key={i}
                style={{
                  color: "var(--ink)",
                  marginTop: 6,
                  fontWeight: 500,
                  borderTop: i ? "1px solid var(--line)" : undefined,
                  paddingTop: i ? 6 : 0,
                }}
              >
                HOP {e.depth} — {e.addresses} address
                {e.addresses === 1 ? "" : "es"} to examine
              </div>
            );
          }
          if (e.type === "node") {
            return (
              <div key={i} style={{ color: "var(--ink-2)", paddingLeft: 12 }}>
                <span style={{ color: roleColour(e.role) }}>■</span>{" "}
                {shortAddress(e.address ?? "", 12, 6)}{" "}
                <span style={{ color: roleColour(e.role) }}>
                  {(e.role ?? "").replace(/_/g, " ")}
                </span>
                {e.terminal && (
                  <span style={{ color: "var(--muted)" }}> · stopped here</span>
                )}
              </div>
            );
          }
          if (e.type === "done") {
            return (
              <div key={i} style={{ color: "var(--low)", marginTop: 6 }}>
                ✓ trace complete — building report
              </div>
            );
          }
          return null;
        })}
      </div>
    </div>
  );
}
