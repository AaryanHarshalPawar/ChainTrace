/** Shared formatting. Kept in one place so a value never renders two ways. */

/** Addresses are 34+ characters; show enough of both ends to compare by eye. */
export function shortAddress(address: string, head = 10, tail = 6): string {
  if (address.length <= head + tail + 1) return address;
  return `${address.slice(0, head)}…${address.slice(-tail)}`;
}

export function usd(value: number | string | null | undefined): string {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "$0";
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}bn`;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}m`;
  if (n >= 1000) return `$${Math.round(n).toLocaleString("en-US")}`;
  return `$${n.toFixed(2)}`;
}

export function percent(ratio: number | null | undefined): string {
  const n = Number(ratio ?? 0) * 100;
  if (n === 0) return "0%";
  if (n < 0.1) return "<0.1%";
  return `${n.toFixed(n < 10 ? 1 : 0)}%`;
}

/** Absolute timestamp plus how long ago — recency decides whether a freeze is
 *  still possible, so both are always shown together. */
export function whenWithAge(iso: string | null | undefined): string {
  if (!iso) return "not established";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "not established";

  const stamp = then.toISOString().slice(0, 16).replace("T", " ") + " UTC";
  const days = Math.floor((Date.now() - then.getTime()) / 86_400_000);
  if (days < 1) return `${stamp} (today)`;
  if (days === 1) return `${stamp} (yesterday)`;
  if (days < 60) return `${stamp} (${days} days ago)`;
  if (days < 730) return `${stamp} (${Math.floor(days / 30)} months ago)`;
  return `${stamp} (${Math.floor(days / 365)} years ago)`;
}

export function riskColor(level: string | null | undefined): string {
  switch (level) {
    case "critical":
      return "var(--critical)";
    case "high":
      return "var(--high)";
    case "medium":
      return "var(--medium)";
    default:
      return "var(--low)";
  }
}

export function riskSoft(level: string | null | undefined): string {
  switch (level) {
    case "critical":
      return "var(--critical-soft)";
    case "high":
      return "var(--high-soft)";
    case "medium":
      return "var(--medium-soft)";
    default:
      return "var(--low-soft)";
  }
}

/** How each node role is drawn. `emphasis` marks the roles that carry the
 *  finding an officer acts on, so they read first in the graph. */
export function roleStyle(role: string): {
  border: string;
  short: string;
  emphasis: boolean;
} {
  switch (role) {
    case "subject":
      return { border: "var(--accent)", short: "reported", emphasis: true };
    case "vasp_deposit":
      return { border: "var(--critical)", short: "deposit addr", emphasis: true };
    case "vasp_hot":
      return { border: "var(--high)", short: "exchange", emphasis: true };
    case "mixer":
      return { border: "var(--critical)", short: "mixer", emphasis: true };
    case "bridge":
      return { border: "var(--medium)", short: "bridge", emphasis: false };
    case "contract":
      return { border: "var(--line-strong)", short: "contract", emphasis: false };
    case "terminal":
      return { border: "var(--muted)", short: "holding", emphasis: false };
    default:
      return { border: "var(--line-strong)", short: "hop", emphasis: false };
  }
}

export function methodLabel(method: string | null | undefined): string {
  switch (method) {
    case "direct_label":
      return "Known address";
    case "deposit_address_heuristic":
      return "Deposit-address sweep";
    case "behavioural_inference":
      return "Behavioural inference";
    case "cluster_match":
      return "Cluster match";
    default:
      return String(method ?? "unknown");
  }
}
