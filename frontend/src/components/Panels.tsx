/**
 * The right-hand column: the answer, the evidence, and what to do about it.
 *
 * Ordering is deliberate and follows the officer's decision, not the data
 * model. The attribution comes first because it names who to contact; the
 * actions come next because they carry deadlines; the risk signals sit below
 * because they justify the first two rather than replacing them.
 */

import type {
  Attribution,
  InvestigativeAction,
  RiskAssessment,
  TraceNode,
} from "../api/client";
import {
  methodLabel,
  percent,
  riskColor,
  riskSoft,
  roleStyle,
  usd,
  whenWithAge,
} from "../format";

const card: React.CSSProperties = {
  background: "var(--surface)",
  border: "1px solid var(--line)",
  borderRadius: "var(--radius)",
  padding: "16px 18px",
  boxShadow: "var(--shadow)",
};

function Row({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 14, padding: "5px 0" }}>
      <span className="label" style={{ flexShrink: 0, paddingTop: 2 }}>
        {label}
      </span>
      <span
        className={mono ? "mono" : undefined}
        style={{ fontSize: 12.5, textAlign: "right", color: "var(--ink)", wordBreak: "break-all" }}
      >
        {value}
      </span>
    </div>
  );
}

export function RiskBanner({ risk, elapsed }: { risk: RiskAssessment; elapsed: number }) {
  const level = risk.level ?? "info";
  return (
    <div
      style={{
        ...card,
        background: riskSoft(level),
        borderColor: riskColor(level),
        borderLeftWidth: 4,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <span
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: 24,
            fontWeight: 700,
            color: riskColor(level),
            letterSpacing: "-0.01em",
          }}
        >
          {String(level).toUpperCase()}
        </span>
        <span className="mono" style={{ fontSize: 13, color: riskColor(level) }}>
          {risk.score}/100
        </span>
        <span className="mono" style={{ fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}>
          traced in {elapsed}s
        </span>
      </div>
      <p style={{ margin: "8px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>{risk.summary}</p>
    </div>
  );
}

export function AttributionCard({ attribution }: { attribution: Attribution }) {
  const deposit = attribution.deposit_address;
  const inferred = attribution.method === "behavioural_inference";

  return (
    <div style={card}>
      <div className="label" style={{ marginBottom: 8 }}>
        Nearest VASP · hop {attribution.hops_from_subject}
      </div>
      <div
        style={{
          fontFamily: "var(--font-serif)",
          fontSize: 17,
          fontWeight: 600,
          marginBottom: 4,
        }}
      >
        {attribution.vasp_name}
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12 }}>
        {methodLabel(attribution.method)} · {percent(attribution.confidence)} confidence
      </div>

      {deposit && (
        // The single most actionable fact on the screen: a hot wallet names
        // nobody, this names one KYC'd account.
        <div
          style={{
            background: "var(--critical-soft)",
            border: "1px solid var(--critical)",
            borderRadius: 4,
            padding: "10px 12px",
            marginBottom: 12,
          }}
        >
          <div className="label" style={{ color: "var(--critical)" }}>
            Deposit address — name this in the notice
          </div>
          <div className="mono" style={{ fontSize: 12, marginTop: 4, wordBreak: "break-all" }}>
            {deposit}
          </div>
        </div>
      )}

      <Row label="Taint" value={`${percent(attribution.taint_ratio)} of outflow`} mono />
      <Row label="Value" value={usd(attribution.value_usd)} mono />
      <Row label="First deposit" value={whenWithAge(attribution.first_deposit_at)} mono />
      <Row label="Last deposit" value={whenWithAge(attribution.last_deposit_at)} mono />
      <Row
        label="FIU-IND"
        value={attribution.fiu_ind_registered ? "Registered — serve directly" : "Not confirmed"}
      />

      {inferred && (
        <p
          style={{
            margin: "12px 0 0",
            fontSize: 11.5,
            color: "var(--high)",
            borderTop: "1px solid var(--line)",
            paddingTop: 10,
          }}
        >
          Identified from behaviour alone, with no verified label. Confirm the
          operator before serving any notice.
        </p>
      )}

      {(attribution.reasoning?.length ?? 0) > 0 && (
        <details style={{ marginTop: 12, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <summary style={{ fontSize: 12, cursor: "pointer", color: "var(--accent)" }}>
            Why we concluded this
          </summary>
          <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12, color: "var(--ink-2)" }}>
            {attribution.reasoning?.map((r, i) => (
              <li key={i} style={{ marginBottom: 5 }}>
                {r}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function ActionsCard({ actions }: { actions: InvestigativeAction[] }) {
  if (!actions.length) return null;
  return (
    <div style={card}>
      <div className="label" style={{ marginBottom: 10 }}>
        Do this now
      </div>
      {actions.map((a) => (
        <div
          key={a.priority}
          style={{
            display: "grid",
            gridTemplateColumns: "22px 1fr",
            gap: 10,
            paddingBottom: 12,
            marginBottom: 12,
            borderBottom: "1px solid var(--line)",
          }}
        >
          <span className="mono" style={{ fontSize: 12, color: "var(--accent)", fontWeight: 600 }}>
            {a.priority}
          </span>
          <div>
            <div style={{ fontSize: 13, fontWeight: 500 }}>{a.action}</div>
            {a.deadline_hint && (
              <span
                className="pill"
                style={{ color: "var(--critical)", marginTop: 6, display: "inline-block" }}
              >
                {a.deadline_hint}
              </span>
            )}
            <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--muted)" }}>{a.rationale}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

export function SignalsCard({ risk }: { risk: RiskAssessment }) {
  if (!risk.signals?.length) return null;
  return (
    <div style={card}>
      <div className="label" style={{ marginBottom: 10 }}>
        Risk signals
      </div>
      {risk.signals.map((s) => (
        <details key={s.code} style={{ borderTop: "1px solid var(--line)", padding: "8px 0" }}>
          <summary style={{ cursor: "pointer", fontSize: 12.5, display: "flex", gap: 8 }}>
            <span className="pill" style={{ color: riskColor(s.level), flexShrink: 0 }}>
              {s.level}
            </span>
            <span>{s.title}</span>
          </summary>
          <p style={{ margin: "8px 0 0 8px", fontSize: 12, color: "var(--ink-2)" }}>{s.detail}</p>
        </details>
      ))}
    </div>
  );
}

export function NodeInspector({ node }: { node: TraceNode }) {
  const style = roleStyle(node.role ?? "intermediary");
  const p = node.profile;
  return (
    <div style={{ ...card, borderLeft: `4px solid ${style.border}` }}>
      <div className="label" style={{ marginBottom: 6 }}>
        Selected address · hop {node.depth}
      </div>
      <div className="mono" style={{ fontSize: 12, wordBreak: "break-all", marginBottom: 10 }}>
        {node.address}
      </div>
      <Row label="Role" value={style.short} />
      <Row label="Taint" value={percent(node.taint_ratio)} mono />
      <Row label="Value in" value={usd(node.value_in_usd)} mono />
      {p && (
        <>
          <Row label="Paid by" value={`${p.unique_senders} addresses`} mono />
          <Row label="Paid to" value={`${p.unique_receivers} addresses`} mono />
          <Row label="Transfers" value={`${p.transfer_count}${p.is_truncated ? "+" : ""}`} mono />
        </>
      )}
      {node.stop_reason && (
        <p
          style={{
            margin: "10px 0 0",
            fontSize: 11.5,
            color: "var(--muted)",
            borderTop: "1px solid var(--line)",
            paddingTop: 8,
          }}
        >
          {node.stop_reason}
        </p>
      )}
      <a
        href={`https://tronscan.org/#/address/${node.address}`}
        target="_blank"
        rel="noreferrer"
        style={{ fontSize: 12, color: "var(--accent)", display: "inline-block", marginTop: 10 }}
      >
        Open in Tronscan ↗
      </a>
    </div>
  );
}

export function ScopeCard({
  stats,
  filtered,
}: {
  stats: Record<string, unknown>;
  filtered: Record<string, number>;
}) {
  const entries = Object.entries(filtered ?? {});
  return (
    <div style={{ ...card, background: "var(--surface-2)" }}>
      <div className="label" style={{ marginBottom: 8 }}>
        Scope of search
      </div>
      <Row label="Addresses" value={String(stats.nodes_explored ?? 0)} mono />
      <Row label="Unreachable" value={String(stats.nodes_unreachable ?? 0)} mono />
      <Row label="Max depth" value={String(stats.max_depth_reached ?? 0)} mono />
      <Row
        label="Complete"
        value={stats.truncated ? "No — budget reached" : "Yes"}
        mono
      />
      {entries.length > 0 && (
        <p style={{ margin: "10px 0 0", fontSize: 11.5, color: "var(--muted)" }}>
          Set aside as immaterial:{" "}
          {entries.map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}.
        </p>
      )}
    </div>
  );
}
