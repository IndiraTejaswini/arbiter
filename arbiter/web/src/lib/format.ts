import type { Outcome, ProvenanceTier, Severity } from "./types";

/**
 * Presentation helpers. These format values the backend produced; none of
 * them invent one.
 */

/**
 * Currency exponents vary — JPY has 0 decimal places, KWD and BHD have 3.
 * Dividing by a hardcoded 100 mis-states every non-2-decimal currency by
 * one or two orders of magnitude, which in a system that moves money is
 * not a cosmetic bug.
 */
export function money(minorUnits: number, currency: string): string {
  let formatter: Intl.NumberFormat;
  try {
    formatter = new Intl.NumberFormat("en-US", { style: "currency", currency });
  } catch {
    return `${minorUnits} ${currency}`; // unknown currency code from the ledger
  }
  const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
  return formatter.format(minorUnits / 10 ** digits);
}

export function percent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function dateTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function date(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function relativeDays(iso: string): string {
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return iso;
  const days = Math.round((target - Date.now()) / 86_400_000);
  if (days === 0) return "today";
  if (days > 0) return `in ${days} day${days === 1 ? "" : "s"}`;
  return `${Math.abs(days)} day${days === -1 ? "" : "s"} overdue`;
}

export function bytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function shortId(id: string, length = 8): string {
  return id.slice(0, length);
}

export function humanize(token: string): string {
  return token.replace(/_/g, " ").toLowerCase();
}

// -- Semantic colour ------------------------------------------------------
// Colour is never the only channel: every consumer of these also renders
// the label, so the meaning survives for a colour-blind reviewer.

export const TIER_STYLE: Record<ProvenanceTier, string> = {
  COMMITTED: "bg-violet-100 text-violet-800 ring-violet-600/20 dark:bg-violet-950 dark:text-violet-300 dark:ring-violet-400/30",
  NETWORK: "bg-sky-100 text-sky-800 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-300 dark:ring-sky-400/30",
  SUBMITTED: "bg-amber-100 text-amber-900 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/30",
  ASSERTED: "bg-neutral-100 text-neutral-700 ring-neutral-500/20 dark:bg-neutral-800 dark:text-neutral-300 dark:ring-neutral-400/30",
};

export const TIER_RANK: Record<ProvenanceTier, number> = {
  COMMITTED: 4, NETWORK: 3, SUBMITTED: 2, ASSERTED: 1,
};

export const TIER_MEANING: Record<ProvenanceTier, string> = {
  COMMITTED: "ADEC-verified — cryptographically proven to predate the dispute",
  NETWORK: "Amex-held — authorization, settlement, AVS/CVV, device, carrier",
  SUBMITTED: "Party-supplied at dispute time, unverified",
  ASSERTED: "Narrative claim with no supporting artifact",
};

export const OUTCOME_STYLE: Record<Outcome, string> = {
  MERCHANT_PREVAILS: "bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/30",
  CARD_MEMBER_PREVAILS: "bg-rose-100 text-rose-800 ring-rose-600/20 dark:bg-rose-950 dark:text-rose-300 dark:ring-rose-400/30",
  SPLIT: "bg-yellow-100 text-yellow-900 ring-yellow-600/20 dark:bg-yellow-950 dark:text-yellow-300 dark:ring-yellow-400/30",
  INSUFFICIENT_EVIDENCE: "bg-neutral-100 text-neutral-700 ring-neutral-500/20 dark:bg-neutral-800 dark:text-neutral-300 dark:ring-neutral-400/30",
  // Deliberately NOT the merchant's colour. Recording an excluded or
  // out-of-time dispute as a merchant win corrupts win rates, the fairness
  // layer's per-rule disparate-impact analysis, and the conformal
  // calibration pool at once — and colouring it like one would tell a
  // reader the same lie the enum exists to prevent. Slate: nobody won.
  CHARGEBACK_INELIGIBLE: "bg-slate-200 text-slate-800 ring-slate-600/20 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-400/30",
};

export const OUTCOME_LABEL: Record<Outcome, string> = {
  MERCHANT_PREVAILS: "Merchant prevails",
  CARD_MEMBER_PREVAILS: "Card member prevails",
  SPLIT: "Split",
  INSUFFICIENT_EVIDENCE: "Insufficient evidence",
  CHARGEBACK_INELIGIBLE: "Not chargeable",
};

/** What each outcome actually means, in the words a party should read. The
 *  last one carries the most weight: a card member told "not chargeable"
 *  must not conclude their rights against the issuer have been decided. */
export const OUTCOME_MEANING: Record<Outcome, string> = {
  MERCHANT_PREVAILS: "The rules were satisfied for the merchant on the evidence available.",
  CARD_MEMBER_PREVAILS: "The rules were satisfied for the card member on the evidence available.",
  SPLIT: "More than one outcome's rules were satisfied at once, or the rulepack reached a partial resolution.",
  INSUFFICIENT_EVIDENCE: "No rule was satisfied. The case does not resolve on the merits as it stands.",
  CHARGEBACK_INELIGIBLE:
    "American Express's chargeback rules give no right to charge this transaction back to the " +
    "merchant under this reason code, so no evidence was weighed. This does not decide the card " +
    "member's separate billing-error rights against the issuer under Reg Z or Reg E.",
};

export const SEVERITY_STYLE: Record<Severity, string> = {
  LOW: "bg-cyan-100 text-cyan-800 ring-cyan-600/20 dark:bg-cyan-950 dark:text-cyan-300 dark:ring-cyan-400/30",
  MEDIUM: "bg-yellow-100 text-yellow-900 ring-yellow-600/20 dark:bg-yellow-950 dark:text-yellow-300 dark:ring-yellow-400/30",
  HIGH: "bg-orange-100 text-orange-900 ring-orange-600/20 dark:bg-orange-950 dark:text-orange-300 dark:ring-orange-400/30",
  CRITICAL: "bg-red-100 text-red-900 ring-red-600/20 dark:bg-red-950 dark:text-red-300 dark:ring-red-400/30",
};

export const STATE_STYLE: Record<string, string> = {
  INTAKE: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
  GATHERING: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  AWAITING_EVIDENCE: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300",
  ANALYSING: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  ADJUDICATED: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  ESCALATED: "bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-300",
  SETTLED: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  DEFLECTED: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
  REOPENED: "bg-yellow-100 text-yellow-900 dark:bg-yellow-950 dark:text-yellow-300",
};

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
