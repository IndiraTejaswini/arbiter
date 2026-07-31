import { cx, dateTime, relativeDays } from "@/lib/format";
import type { DisputeCase } from "@/lib/types";
import { Badge } from "./ui";

/**
 * The regulatory clock for one case.
 *
 * These columns existed from the first migration, went unread until the
 * deadline sweeper was built, and then went unexposed — so the sweeper's
 * work was invisible to the people it was done for. A card member cannot
 * see that their Reg E provisional credit is due; a merchant cannot see
 * how long they have left to respond; an analyst cannot see that a
 * statutory deadline has already been breached.
 *
 * Reg Z and Reg E are genuinely different clocks and are rendered as such
 * rather than collapsed into one generic "deadline" row.
 */
export default function RegulatoryClock({ dispute }: { dispute: DisputeCase }) {
  const regE = dispute.reg_regime === "REG_E";

  const rows: {
    label: string;
    deadline: string | null;
    met: string | null;
    basis: string;
    metLabel: string;
  }[] = [
    {
      label: regE ? "Determination" : "Written acknowledgment",
      deadline: dispute.ack_deadline,
      met: dispute.acknowledged_at,
      metLabel: "acknowledged",
      basis: regE
        ? "12 CFR 1005.11(c)(1) — 10 business days"
        : "12 CFR 1026.13(b)(1) — 30 days",
    },
    {
      label: "Merchant response window",
      deadline: dispute.merchant_response_deadline,
      met: dispute.merchant_responded ? "responded" : dispute.merchant_window_expired_at,
      metLabel: dispute.merchant_responded ? "responded" : "expired",
      basis: "20 days from the Amex Central Site Business Date",
    },
    {
      label: "Resolution",
      deadline: dispute.resolve_deadline,
      met: null,
      metLabel: "",
      basis: regE
        ? "12 CFR 1005.11(c)(3) — extended investigation limit"
        : "12 CFR 1026.13(c)(2) — two billing cycles, never beyond 90 days",
    },
  ];

  if (regE) {
    rows.splice(1, 0, {
      label: "Provisional credit",
      deadline: dispute.provisional_credit_deadline,
      met: dispute.provisional_credit_issued_at,
      metLabel: "issued",
      basis: "12 CFR 1005.11(c)(2) — due while the investigation continues, whoever ultimately prevails",
    });
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge className={regE
          ? "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300"
          : "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"}>
          {dispute.reg_regime.replace("_", " ")}
        </Badge>
        <span className="text-2xs text-neutral-500 dark:text-neutral-400">
          {regE
            ? "Prepaid/debit — Regulation E. Business-day clocks and a provisional-credit obligation."
            : "Credit — Regulation Z. Calendar-day clocks; the analogous protection is payment withholding, not credit issuance."}
        </span>
      </div>

      <ul className="space-y-2">
        {rows.map((row) => {
          if (!row.deadline) return null;
          const overdue = !row.met && new Date(row.deadline).getTime() < Date.now();
          return (
            <li key={row.label} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <div className="min-w-0">
                <p className="text-xs font-medium text-neutral-800 dark:text-neutral-200">{row.label}</p>
                <p className="text-2xs text-neutral-500 dark:text-neutral-500">{row.basis}</p>
              </div>
              <div className="text-right">
                <p className={cx(
                  "text-xs font-medium tabular-nums",
                  row.met ? "text-emerald-700 dark:text-emerald-400"
                    : overdue ? "text-red-700 dark:text-red-400"
                    : "text-neutral-700 dark:text-neutral-300",
                )}>
                  {row.met
                    ? row.metLabel
                    : overdue ? "breached" : relativeDays(row.deadline)}
                </p>
                <p className="text-2xs text-neutral-500 dark:text-neutral-500">
                  {dateTime(row.deadline)}
                </p>
              </div>
            </li>
          );
        })}
      </ul>

      {!dispute.merchant_responded && dispute.merchant_window_expired_at && (
        <p className="rounded-lg border border-emerald-300 bg-emerald-50 p-2.5 text-2xs leading-relaxed text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
          <strong className="font-semibold">The merchant window expired and the case was not conceded.</strong>{" "}
          Advocate-M built the merchant's case from Amex-held data and the referee decided on the
          merits — the direct fix for R03/R13, where cases are lost on a mailbox rather than on facts.
        </p>
      )}
    </div>
  );
}
