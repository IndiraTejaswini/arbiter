import { cx, dateTime, humanize } from "@/lib/format";
import type { EligibilityRecord } from "@/lib/types";
import { Badge, Disclosure, Mono } from "./ui";

/**
 * The chargeback-right gate's finding for one case.
 *
 * This runs BEFORE anything else in the pipeline and answers a different
 * question from the referee's: not "who is right?" but "may American Express
 * charge this back at all?". Amex's published merchant guide gives every
 * reason code a "Maximum time a dispute can be raised" window and an
 * "Excluded Transactions" list, and both remove the chargeback right
 * outright. When the gate closes, no evidence is loaded, no advocate runs,
 * and the referee is never called.
 *
 * The backend has recorded this on every decision since the gate shipped and
 * nothing rendered it, so the most consequential thing that can happen to a
 * dispute — being told it was never chargeable — arrived in the UI as a
 * blank badge next to an empty proof tree.
 *
 * Three distinctions this view refuses to collapse:
 *
 *   1. **Not chargeable ≠ merchant wins.** No evidence was weighed. The
 *      copy says so, because a card member reading "you lost" when the truth
 *      is "this route was never open" has been told something false.
 *   2. **Not chargeable ≠ no rights.** The card member's Reg Z / Reg E
 *      billing-error rights against the issuer are untouched and run on
 *      their own clocks.
 *   3. **Unknown ≠ clear.** An attribute the ledger did not supply cannot
 *      fire an exclusion — unknown fails OPEN here, and only here. Those
 *      gaps are shown rather than swallowed, because a gate that silently
 *      stops running on missing data is worse than one that never ran.
 */
export default function ChargebackRightPanel({
  eligibility,
}: { eligibility: EligibilityRecord }) {
  const fired = eligibility.exclusions_fired ?? [];
  const branches = eligibility.filing_window?.branches ?? [];
  const undetermined = eligibility.undetermined_attributes ?? [];
  const windowTimely = eligibility.filing_window?.timely ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          className={eligibility.available
            ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
            : "bg-slate-200 text-slate-800 dark:bg-slate-800 dark:text-slate-200"}
        >
          {eligibility.available ? "chargeback right available" : "no chargeback right"}
        </Badge>
        {eligibility.network_code && (
          <Badge title="The four-digit code the Amex chargeback guide and a merchant's own 'Resolve Disputes' screen use for this dispute.">
            Amex code {eligibility.network_code}
          </Badge>
        )}
        <Badge title="Every 'Excluded Transactions' bullet on this reason code was evaluated, not just the ones that fired.">
          {eligibility.exclusions_evaluated} exclusion
          {eligibility.exclusions_evaluated === 1 ? "" : "s"} evaluated
        </Badge>
      </div>

      <p className="text-xs leading-relaxed text-neutral-700 dark:text-neutral-300">
        {eligibility.reason}
      </p>

      {!eligibility.available && (
        <div className="rounded-lg border border-slate-300 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-900/60">
          <p className="text-xs font-semibold text-slate-900 dark:text-slate-100">
            This is not a finding that the merchant won
          </p>
          <p className="mt-1 text-2xs leading-relaxed text-slate-700 dark:text-slate-300">
            No evidence was weighed and no rule was evaluated — the network gives no right to
            charge this transaction back under this reason code. Recording it as a merchant
            win would corrupt win rates, the fairness layer's per-rule analysis, and the
            conformal calibration pool at once, so it is kept distinct.
          </p>
          <p className="mt-2 text-2xs leading-relaxed text-slate-700 dark:text-slate-300">
            <strong className="font-semibold">The card member's rights are unaffected.</strong>{" "}
            Regulation Z (12 CFR 1026.13) and Regulation E (12 CFR 1005.11) impose obligations
            on the issuer that continue on their own clocks. A dispute that misses the network
            window is still a billing error Amex must resolve — it just resolves at Amex's cost
            rather than the merchant's.
          </p>
        </div>
      )}

      {fired.length > 0 && (
        <section>
          <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wide text-neutral-500">
            Exclusions that fired
          </h3>
          <ul className="space-y-2">
            {fired.map((exclusion) => (
              <li
                key={exclusion.exclusion_id}
                className="rounded-lg border border-slate-300 p-3 dark:border-slate-700"
              >
                <Mono className="font-semibold text-neutral-800 dark:text-neutral-200">
                  {exclusion.exclusion_id}
                </Mono>
                <p className="mt-1 text-xs leading-relaxed text-neutral-700 dark:text-neutral-300">
                  {exclusion.description}
                </p>
                {exclusion.satisfied_conditions.length > 0 && (
                  <div className="mt-2">
                    <p className="text-2xs font-medium uppercase tracking-wide text-neutral-500">
                      Established
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {exclusion.satisfied_conditions.map((condition, i) => (
                        <li key={i}>
                          <Mono>{condition}</Mono>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {exclusion.legal_basis && (
                  <div className="mt-2">
                    <Disclosure summary="Basis">
                      <p className="text-2xs leading-relaxed text-neutral-600 dark:text-neutral-400">
                        {exclusion.legal_basis}
                      </p>
                    </Disclosure>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {branches.length > 0 && (
        <section>
          <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wide text-neutral-500">
            Filing window
            {windowTimely === true && " — in time"}
            {windowTimely === false && " — closed"}
            {windowTimely === null && " — could not be evaluated"}
          </h3>
          <ul className="space-y-1.5">
            {branches.map((branch) => (
              <li
                key={branch.branch_id}
                className={cx(
                  "flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 rounded-lg border p-2.5",
                  branch.timely === false
                    ? "border-slate-300 bg-slate-50 dark:border-slate-700 dark:bg-slate-900/60"
                    : "border-neutral-200 dark:border-neutral-800",
                )}
              >
                <div className="min-w-0">
                  <Mono className="font-semibold text-neutral-800 dark:text-neutral-200">
                    {branch.branch_id}
                  </Mono>
                  <p className="text-2xs text-neutral-500 dark:text-neutral-400">
                    {branch.anchor_attribute
                      ? <>measured from {humanize(branch.anchor_attribute)}
                          {branch.anchor_at && <> ({dateTime(branch.anchor_at)})</>}</>
                      : "the ledger did not supply this branch's anchor date"}
                  </p>
                </div>
                <div className="text-right">
                  <p className={cx(
                    "text-xs font-medium",
                    branch.timely === true ? "text-emerald-700 dark:text-emerald-400"
                      : branch.timely === false ? "text-slate-700 dark:text-slate-300"
                      : "text-neutral-500",
                  )}>
                    {branch.timely === true ? "in time"
                      : branch.timely === false ? (branch.capped_out ? "past the absolute cap" : "closed")
                      : "undetermined"}
                  </p>
                  {branch.deadline && (
                    <p className="text-2xs text-neutral-500 dark:text-neutral-400">
                      {dateTime(branch.deadline)}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {branches.length > 1 && (
            <p className="mt-1.5 text-2xs leading-relaxed text-neutral-500 dark:text-neutral-400">
              The guide lists these as alternatives, so any branch still open keeps the dispute
              in time. Where a branch names more than one anchor date, the EARLIEST known one
              governs — the guide's "whichever occurred first", which an OR over start dates
              would get exactly backwards.
            </p>
          )}
        </section>
      )}

      {undetermined.length > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/40">
          <p className="text-xs font-semibold text-amber-900 dark:text-amber-200">
            An exclusion could not be evaluated
          </p>
          <p className="mt-1 text-2xs leading-relaxed text-amber-800 dark:text-amber-300">
            The ledger did not supply {undetermined.map(humanize).join(", ")}, which an
            exclusion or filing window on this reason code turns on. Unknown never excludes:
            this case proceeded to the merits rather than being barred on missing data.
            That fail direction is deliberate and opposite to this system's usual one —
            every other gate here fails closed <em>for the card member's protection</em>,
            whereas an exclusion firing removes their dispute right with nothing after it.
          </p>
          <div className="mt-2 flex flex-wrap gap-1">
            {undetermined.map((attribute) => (
              <Badge key={attribute} className="bg-white/60 dark:bg-black/20">
                <Mono className="text-inherit">{attribute}</Mono>
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
