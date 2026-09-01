/**
 * The investigation report.
 *
 * This is the deliverable. Everything else in the app exists to produce this
 * page, and an officer acts on what it says -- so it is written as a document
 * to be filed, not a dashboard to be browsed.
 *
 * Three rules govern the layout:
 *
 * 1. **The finding leads.** Which VASP, which deposit address, how much, how
 *    long ago. An officer reading only the first screen must still know who to
 *    contact and how urgently.
 * 2. **Every claim carries its evidence.** Transaction hashes, sources and
 *    methods are on the page, not behind a click, because the report has to
 *    stand on its own once printed.
 * 3. **The limits are stated, not hidden.** What was searched, what was set
 *    aside, and what the system could not determine. A report that overstates
 *    its certainty is worse than no report.
 */

import type { TraceResult } from "../api/client";
import {
  methodLabel,
  percent,
  riskColor,
  riskSoft,
  roleStyle,
  shortAddress,
  usd,
  whenWithAge,
} from "../format";

interface Props {
  result: TraceResult;
  caseId: string;
}

function Section({
  title,
  n,
  children,
}: {
  title: string;
  n: string;
  children: React.ReactNode;
}) {
  return (
    <section style={{ marginBottom: 26 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          borderBottom: "1px solid var(--line-strong)",
          paddingBottom: 6,
          marginBottom: 14,
        }}
      >
        <span className="mono" style={{ fontSize: 11, color: "var(--accent)" }}>
          {n}
        </span>
        <h2
          style={{
            margin: 0,
            fontFamily: "var(--font-serif)",
            fontSize: 15,
            fontWeight: 600,
            letterSpacing: "0.01em",
          }}
        >
          {title}
        </h2>
      </div>
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "170px 1fr",
        gap: 14,
        padding: "7px 0",
        borderBottom: "1px solid var(--line)",
      }}
    >
      <span className="label" style={{ paddingTop: 2 }}>
        {label}
      </span>
      <span style={{ fontSize: 13, wordBreak: "break-all" }}>{value}</span>
    </div>
  );
}

/** The horizontal hop chain. Taint is printed under each node because it is
 *  what tells an officer which lead is worth a preservation request. */
function TracePath({ result }: { result: TraceResult }) {
  const nodes = [...(result.nodes ?? [])].sort((a, b) => a.depth - b.depth);
  if (!nodes.length) return null;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 0,
        overflowX: "auto",
        padding: "6px 2px 2px",
      }}
    >
      {nodes.map((node, i) => {
        const style = roleStyle(node.role ?? "intermediary");
        const isLast = i === nodes.length - 1;
        return (
          <div key={node.address} style={{ display: "flex", alignItems: "flex-start" }}>
            <div style={{ textAlign: "center", minWidth: 128 }}>
              <div
                style={{
                  width: 76,
                  height: 76,
                  margin: "0 auto",
                  borderRadius: "50%",
                  border: `2px solid ${style.border}`,
                  background: "var(--surface-2)",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 1,
                }}
              >
                <span
                  style={{ fontSize: 11, fontWeight: 600, color: style.border }}
                >
                  {node.depth === 0 ? "SEED" : `HOP ${node.depth}`}
                </span>
                <span
                  className="mono"
                  style={{ fontSize: 8.5, color: "var(--muted)", padding: "0 4px" }}
                >
                  {style.short}
                </span>
              </div>
              <div
                className="mono"
                style={{ fontSize: 10, marginTop: 6, color: "var(--ink-2)" }}
              >
                {shortAddress(node.address, 6, 4)}
              </div>
              <div
                className="mono"
                style={{ fontSize: 11, marginTop: 2, color: style.border, fontWeight: 500 }}
              >
                {node.depth === 0 ? "origin" : `${percent(node.taint_ratio)} taint`}
              </div>
            </div>

            {!isLast && (
              <div
                style={{
                  width: 52,
                  height: 2,
                  marginTop: 37,
                  background: "var(--line-strong)",
                  flexShrink: 0,
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export function Report({ result, caseId }: Props) {
  const risk = result.risk;
  const primary = result.attributions?.[0] ?? null;
  const level = risk?.level ?? "info";
  const generated = new Date(result.generated_at);
  const subject = result.nodes?.find((n) => n.depth === 0);

  const evidenceHashes = primary?.evidence_tx_hashes ?? [];
  const filtered = Object.entries(result.filtered_summary ?? {});

  // When the attribution sits at hop 0 the reported address *is* the entity,
  // so taint is 100% by definition and measures nothing. Printing it as a
  // headline reads like a strong claim when it is an empty one.
  const selfAttributed = primary?.hops_from_subject === 0;

  return (
    <article
      style={{
        maxWidth: 860,
        margin: "0 auto",
        padding: "26px 30px 60px",
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius)",
      }}
    >
      {/* ---------- masthead ---------- */}
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 20,
          borderBottom: "2px solid var(--ink)",
          paddingBottom: 16,
          marginBottom: 22,
        }}
      >
        <div>
          <div className="label">Blockchain investigation report</div>
          <h1
            style={{
              margin: "6px 0 2px",
              fontFamily: "var(--font-serif)",
              fontSize: 26,
              fontWeight: 700,
              letterSpacing: "-0.015em",
            }}
          >
            {caseId}
          </h1>
          <div className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
            Generated {generated.toISOString().slice(0, 16).replace("T", " ")} UTC
            {" · "}
            ChainTrace v0.1
          </div>
        </div>

        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div
            className="pill"
            style={{
              color: riskColor(level),
              background: riskSoft(level),
              fontSize: 11,
              padding: "6px 12px",
            }}
          >
            {String(level).toUpperCase()} RISK
          </div>
          <div
            className="mono"
            style={{ fontSize: 22, fontWeight: 600, marginTop: 8, color: riskColor(level) }}
          >
            {risk?.score ?? 0}
            <span style={{ fontSize: 12, color: "var(--muted)" }}>/100</span>
          </div>
        </div>
      </header>

      {/* ---------- headline metrics ---------- */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 12,
          marginBottom: 24,
        }}
      >
        {[
          {
            k: "Traced value",
            v: usd(primary?.value_usd ?? subject?.value_in_usd),
          },
          // Swapped for a figure that carries information when taint cannot.
          selfAttributed
            ? {
                k: "Paid in by",
                v: subject?.profile
                  ? `${subject.profile.unique_senders}${subject.profile.is_truncated ? "+" : ""}`
                  : "—",
              }
            : { k: "Taint reaching VASP", v: percent(primary?.taint_ratio) },
          {
            k: "Hops to VASP",
            v: !primary
              ? "—"
              : primary.hops_from_subject === 0
                ? "0 · direct"
                : String(primary.hops_from_subject),
          },
          {
            k: "Addresses traced",
            v: String(result.stats?.nodes_explored ?? 0),
          },
        ].map((tile) => (
          <div
            key={tile.k}
            style={{
              background: "var(--surface-2)",
              border: "1px solid var(--line)",
              borderRadius: 6,
              padding: "12px 14px",
            }}
          >
            <div className="label">{tile.k}</div>
            <div
              className="mono"
              style={{ fontSize: 20, fontWeight: 600, marginTop: 4 }}
            >
              {tile.v}
            </div>
          </div>
        ))}
      </div>

      {/* ---------- 1. subject ---------- */}
      <Section n="01" title="Reported address">
        <Field
          label="Wallet address"
          value={
            <span className="mono" style={{ fontSize: 12.5 }}>
              {result.subject_address}
            </span>
          }
        />
        <Field label="Blockchain" value={String(result.chain).toUpperCase()} />
        {subject?.profile && (
          <>
            <Field
              label="Paid in by"
              value={`${subject.profile.unique_senders} distinct addresses${
                subject.profile.is_truncated ? " (observation window truncated)" : ""
              }`}
            />
            <Field
              label="Total received"
              value={usd(subject.profile.total_received_usd)}
            />
            <Field
              label="Activity window"
              value={
                subject.profile.first_seen
                  ? `${whenWithAge(subject.profile.first_seen)} → ${whenWithAge(
                      subject.profile.last_seen,
                    )}`
                  : "not established"
              }
            />
          </>
        )}
      </Section>

      {/* ---------- 2. the finding ---------- */}
      <Section n="02" title="Principal finding">
        {primary ? (
          <>
            <div
              style={{
                background: riskSoft(level),
                border: `1px solid ${riskColor(level)}`,
                borderRadius: 6,
                padding: "14px 16px",
                marginBottom: 14,
              }}
            >
              <div style={{ fontSize: 14, lineHeight: 1.6 }}>
                {selfAttributed ? (
                  <>
                    The reported address is itself identified as{" "}
                    <strong>{primary.vasp_name}</strong>
                    {primary.category === "sanctioned"
                      ? ", an entity designated on the OFAC sanctions list."
                      : "."}{" "}
                    No onward tracing was required to reach this finding.
                  </>
                ) : (
                  <>
                    Funds from the reported address reach{" "}
                    <strong>{primary.vasp_name}</strong> after{" "}
                    <strong>{primary.hops_from_subject}</strong> hop
                    {primary.hops_from_subject === 1 ? "" : "s"}, carrying{" "}
                    <strong>{percent(primary.taint_ratio)}</strong> of the value
                    that left the wallet.
                  </>
                )}
              </div>
            </div>

            {primary.deposit_address && (
              // The operative line of the whole report: an omnibus hot wallet
              // is shared by millions and names nobody, whereas this maps to
              // one KYC'd account.
              <div
                style={{
                  background: "var(--critical-soft)",
                  border: "1px solid var(--critical)",
                  borderRadius: 6,
                  padding: "14px 16px",
                  marginBottom: 16,
                }}
              >
                <div className="label" style={{ color: "var(--critical)" }}>
                  Deposit address — name this in the preservation notice
                </div>
                <div
                  className="mono"
                  style={{ fontSize: 14, marginTop: 6, fontWeight: 500 }}
                >
                  {primary.deposit_address}
                </div>
                <p
                  style={{
                    margin: "8px 0 0",
                    fontSize: 11.5,
                    color: "var(--ink-2)",
                  }}
                >
                  A deposit address is assigned to a single customer and maps to
                  one KYC record. The exchange's shared hot wallet does not
                  identify any individual and should not be named alone.
                </p>
              </div>
            )}

            <Field label="Attribution method" value={methodLabel(primary.method)} />
            <Field label="Confidence" value={percent(primary.confidence)} />
            {!selfAttributed && (
              <Field
                label="Exchange hot wallet"
                value={<span className="mono">{primary.matched_address}</span>}
              />
            )}
            {!selfAttributed && (
              <Field
                label="Taint reaching VASP"
                value={`${percent(primary.taint_ratio)} of the value that left the reported address`}
              />
            )}
            <Field
              label={selfAttributed ? "Value received" : "Value reaching VASP"}
              value={usd(primary.value_usd)}
            />
            {/* Deposit timing describes funds arriving at a VASP along a
                traced path. With no onward hop there is no such event, and
                printing "not established" twice implies a gap in the data
                rather than a question that does not arise. */}
            {!selfAttributed && (
              <>
                <Field
                  label="First deposit"
                  value={whenWithAge(primary.first_deposit_at)}
                />
                <Field
                  label="Most recent deposit"
                  value={whenWithAge(primary.last_deposit_at)}
                />
              </>
            )}
            {primary.jurisdiction && (
              <Field label="Jurisdiction" value={primary.jurisdiction} />
            )}
            {/* FIU-IND is a register of virtual-asset businesses. Asking
                whether a sanctioned individual is registered on it is a
                category error, and an officer reading it would rightly
                distrust the rest of the page. */}
            {primary.category === "sanctioned" ? (
              <Field
                label="Legal posture"
                value="OFAC-designated. This is a sanctions matter before it is a recovery matter — escalate to the FIU-IND desk rather than serving a preservation request."
              />
            ) : (
              <Field
                label="FIU-IND registration"
                value={
                  primary.fiu_ind_registered
                    ? "Registered — an Indian LEA may serve notice directly"
                    : "Not confirmed — verify before serving; otherwise route via MLAT or SAHYOG"
                }
              />
            )}

            {(primary.reasoning?.length ?? 0) > 0 && (
              <div style={{ marginTop: 14 }}>
                <div className="label" style={{ marginBottom: 6 }}>
                  Basis for this conclusion
                </div>
                <ul
                  style={{
                    margin: 0,
                    paddingLeft: 18,
                    fontSize: 12.5,
                    color: "var(--ink-2)",
                  }}
                >
                  {primary.reasoning?.map((r, i) => (
                    <li key={i} style={{ marginBottom: 4 }}>
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        ) : (
          <p style={{ fontSize: 13, color: "var(--ink-2)", margin: 0 }}>
            No VASP could be attributed within the searched depth. Funds may
            still be resting on-chain, or the trail extends beyond the search
            budget. Re-run with a higher hop limit before concluding the trail
            has ended.
          </p>
        )}
      </Section>

      {/* ---------- 3. path ---------- */}
      <Section n="03" title="Fund flow">
        <TracePath result={result} />
        {(result.edges?.length ?? 0) > 0 && (
          <div style={{ marginTop: 16, overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 11.5,
                minWidth: 520,
              }}
            >
              <thead>
                <tr>
                  {["From", "To", "Asset", "Amount", "On-chain value"].map((h) => (
                    <th
                      key={h}
                      className="label"
                      style={{
                        textAlign: h === "Amount" || h === "Value" ? "right" : "left",
                        padding: "0 10px 6px 0",
                        borderBottom: "1px solid var(--line-strong)",
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...(result.edges ?? [])]
                  .sort((a, b) => Number(b.total_usd) - Number(a.total_usd))
                  .slice(0, 12)
                  .map((e, i) => (
                    <tr key={i}>
                      <td className="mono" style={{ padding: "6px 10px 6px 0", borderBottom: "1px solid var(--line)" }}>
                        {shortAddress(e.source, 8, 5)}
                      </td>
                      <td className="mono" style={{ padding: "6px 10px 6px 0", borderBottom: "1px solid var(--line)" }}>
                        {shortAddress(e.target, 8, 5)}
                      </td>
                      <td style={{ padding: "6px 10px 6px 0", borderBottom: "1px solid var(--line)" }}>
                        {e.asset_symbol}
                      </td>
                      <td className="mono" style={{ padding: "6px 10px 6px 0", textAlign: "right", borderBottom: "1px solid var(--line)" }}>
                        {Number(e.total_amount).toLocaleString("en-US", { maximumFractionDigits: 8 })}
                      </td>
                      <td className="mono" style={{ padding: "6px 0", textAlign: "right", borderBottom: "1px solid var(--line)" }}>
                        {usd(e.total_usd)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {/* Without this note the report appears to contradict itself: the
                headline tile can read a few hundred dollars while an edge below
                reads billions. Both are true -- the edge carries that wallet's
                entire flow, of which only the traced share is attributable to
                this complaint. */}
            <p
              style={{
                margin: "10px 0 0",
                fontSize: 11,
                color: "var(--muted)",
                lineHeight: 1.5,
              }}
            >
              <strong>On-chain value</strong> is the total moved across that
              edge by all parties. The <strong>traced value</strong> shown above
              is the portion attributable to this complaint, capped by what the
              reported address actually received — it is deliberately the
              smaller, defensible figure.
            </p>
          </div>
        )}
      </Section>

      {/* ---------- 4. actions ---------- */}
      {(result.recommended_actions?.length ?? 0) > 0 && (
        <Section n="04" title="Recommended action">
          {result.recommended_actions?.map((a) => (
            <div
              key={a.priority}
              style={{
                display: "grid",
                gridTemplateColumns: "26px 1fr",
                gap: 12,
                padding: "10px 0",
                borderBottom: "1px solid var(--line)",
              }}
            >
              <span
                className="mono"
                style={{ fontSize: 13, color: "var(--accent)", fontWeight: 600 }}
              >
                {a.priority}
              </span>
              <div>
                <div style={{ fontSize: 13.5, fontWeight: 500, marginBottom: 4 }}>
                  {a.action}
                </div>
                {a.deadline_hint && (
                  <span
                    className="pill"
                    style={{ color: "var(--critical)", marginRight: 8 }}
                  >
                    {a.deadline_hint}
                  </span>
                )}
                <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--muted)" }}>
                  {a.rationale}
                </p>
              </div>
            </div>
          ))}
        </Section>
      )}

      {/* ---------- 5. evidence ---------- */}
      <Section n="05" title="Evidence">
        <Field
          label="Transaction hashes"
          value={
            evidenceHashes.length ? (
              <div className="mono" style={{ fontSize: 11 }}>
                {evidenceHashes.slice(0, 8).map((h) => (
                  <div key={h} style={{ marginBottom: 2 }}>
                    {h}
                  </div>
                ))}
              </div>
            ) : (
              "none captured on this path"
            )
          }
        />
        <Field
          label="Data sources"
          value={
            result.chain === "bitcoin"
              ? "mempool.space (Bitcoin); OFAC SDN sanctions list"
              : "TronGrid (TRON); OFAC SDN sanctions list"
          }
        />
        <Field
          label="Valuation basis"
          value="Stablecoins at par; volatile assets at spot price on the date of this report, not on the transaction date"
        />
        <Field
          label="Value attribution"
          value="Haircut method — value splits proportionally at each branch, so traced value can never exceed the amount that entered the graph"
        />
      </Section>

      {/* ---------- 6. risk ---------- */}
      {(risk?.signals?.length ?? 0) > 0 && (
        <Section n="06" title="Risk assessment">
          <p style={{ fontSize: 13, marginTop: 0, color: "var(--ink-2)" }}>
            {risk?.summary}
          </p>
          {risk?.signals?.map((s) => (
            <div
              key={s.code}
              style={{ padding: "9px 0", borderBottom: "1px solid var(--line)" }}
            >
              <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                <span className="pill" style={{ color: riskColor(s.level) }}>
                  {s.level}
                </span>
                <strong style={{ fontSize: 13 }}>{s.title}</strong>
              </div>
              <p style={{ margin: "5px 0 0", fontSize: 12, color: "var(--muted)" }}>
                {s.detail}
              </p>
            </div>
          ))}
        </Section>
      )}

      {/* ---------- 7. limits ---------- */}
      <Section n="07" title="Scope and limitations">
        <Field
          label="Addresses examined"
          value={`${result.stats?.nodes_explored ?? 0} examined, ${
            result.stats?.nodes_unreachable ?? 0
          } could not be retrieved`}
        />
        <Field
          label="Maximum depth reached"
          value={`${result.stats?.max_depth_reached ?? 0} hops`}
        />
        <Field
          label="Search completed"
          value={
            result.stats?.truncated
              ? `No — ${result.stats?.truncation_reason ?? "search budget reached"}. Attributions beyond this point have not been evaluated.`
              : "Yes — the trail terminated naturally"
          }
        />
        {filtered.length > 0 && (
          <Field
            label="Set aside as immaterial"
            value={filtered
              .map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`)
              .join(", ")}
          />
        )}
        {primary?.method === "behavioural_inference" && (
          <Field
            label="Attribution caveat"
            value="The VASP was identified from transaction behaviour alone and carries no verified label. Confirm the operator independently before serving any notice."
          />
        )}
        {(result.warnings?.length ?? 0) > 0 && (
          <Field
            label="Warnings"
            value={result.warnings?.map((w, i) => (
              <div key={i} style={{ marginBottom: 3 }}>
                {w}
              </div>
            ))}
          />
        )}
      </Section>

      <footer
        style={{
          borderTop: "1px solid var(--line-strong)",
          paddingTop: 12,
          fontSize: 10.5,
          color: "var(--muted)",
          lineHeight: 1.5,
        }}
      >
        Produced by ChainTrace for Smart India Hackathon problem statement
        26183. Attributions marked as inferred are analytical conclusions, not
        confirmed facts, and should be verified with the named VASP before
        enforcement action. Blockchain data is public and independently
        verifiable using the transaction hashes above.
      </footer>
    </article>
  );
}
