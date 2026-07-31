// import { Link } from "react-router-dom";
// import {
//   Async, Badge, Card, CardBody, CardHeader, EmptyState, Mono, Stat,
//   Table, TableWrap, Td, Th,
// } from "@/components/ui";
// import { api } from "@/lib/api";
// import { STATE_STYLE, cx, dateTime, humanize, money, relativeDays } from "@/lib/format";
// import { getSession, isPrivileged } from "@/lib/session";
// import type { AtRiskResponse, CaseListResponse, ReadyResponse } from "@/lib/types";
// import { useApi } from "@/lib/useApi";

// /**
//  * Overview.
//  *
//  * Every number here is a count the API returned. Nothing is estimated,
//  * sampled, or hardcoded — where a figure cannot be obtained from the
//  * backend it is simply not shown, rather than filled with a placeholder.
//  */
// export default function OverviewPage() {
//   const session = getSession();
//   const privileged = isPrivileged(session);

//   const cases = useApi<CaseListResponse>(() => api.listCases({ limit: 200 }), []);
//   const ready = useApi<ReadyResponse>(() => api.ready(), []);
//   const atRisk = useApi<AtRiskResponse>(() => api.casesAtRisk(14), [], { enabled: privileged });

//   return (
//     <>
//       <header className="mb-6">
//         <p className="text-2xs font-semibold uppercase tracking-widest text-brand-600 dark:text-brand-400">
//           {session?.role === "CARD_MEMBER" ? "Card member portal"
//             : session?.role === "MERCHANT" ? "Merchant console"
//             : "Analyst workbench"}
//         </p>
//         <h1 className="mt-1 text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-50">
//           Overview
//         </h1>
//         <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">
//           Disputes are adjudicated by a deterministic rule engine that emits a proof tree.
//           When it cannot decide, it abstains and a human decides — abstention is a
//           first-class output, never a default outcome.
//         </p>
//       </header>

//       <div className="grid gap-4 lg:grid-cols-3">
//         <Card className="lg:col-span-2">
//           <CardHeader
//             title="Case portfolio"
//             subtitle={
//               privileged
//                 ? "Every case in the system."
//                 : "Scoped to you by the API — a party token cannot widen its own scope."
//             }
//           />
//           <CardBody>
//             <Async state={cases} label="cases">
//               {(data) => <PortfolioStats data={data} />}
//             </Async>
//           </CardBody>
//         </Card>

//         <Card>
//           <CardHeader
//             title="Adjudication readiness"
//             subtitle="Per reason code. An under-calibrated code escalates every case."
//           />
//           <CardBody>
//             <Async state={ready} label="service readiness">
//               {(data) => (
//                 <div className="space-y-2.5">
//                   {Object.entries(data.calibration).length === 0 && (
//                     <p className="text-xs text-neutral-500">No rulepacks loaded.</p>
//                   )}
//                   {Object.entries(data.calibration).map(([code, info]) => (
//                     <div key={code} className="flex items-center justify-between gap-2">
//                       <span className="font-mono text-xs font-medium text-neutral-800 dark:text-neutral-200">
//                         {code}
//                       </span>
//                       <div className="flex items-center gap-2">
//                         <span className="text-2xs tabular-nums text-neutral-500">
//                           n={info.n}
//                         </span>
//                         <Badge className={info.calibrated
//                           ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
//                           : "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300"}>
//                           {info.calibrated ? "calibrated" : "escalates all"}
//                         </Badge>
//                       </div>
//                     </div>
//                   ))}
//                   {data.note && (
//                     <p className="border-t border-neutral-200 pt-2.5 text-2xs leading-relaxed text-amber-700 dark:border-neutral-800 dark:text-amber-400">
//                       {data.note}
//                     </p>
//                   )}
//                 </div>
//               )}
//             </Async>
//           </CardBody>
//         </Card>
//       </div>

//       {privileged && (
//         <Card className="mt-4">
//           <CardHeader
//             title="Regulatory clock — cases at risk"
//             subtitle="Open cases whose statutory resolution deadline falls within 14 days. Reg Z: 30-day acknowledgment, 90-day resolution. Reg E: 10 business days."
//             actions={<Link to="/operations"><Badge>Operations →</Badge></Link>}
//           />
//           <CardBody>
//             <Async
//               state={atRisk}
//               label="cases at risk"
//               isEmpty={(d) => d.cases.length === 0}
//               empty={<EmptyState title="No case is within 14 days of its resolution deadline" />}
//             >
//               {(data) => (
//                 <TableWrap>
//                   <Table ariaLabel="Cases approaching a regulatory deadline">
//                     <thead>
//                       <tr>
//                         <Th>Case</Th><Th>Reason</Th><Th>Regime</Th>
//                         <Th>State</Th><Th>Resolve by</Th><Th>Remaining</Th>
//                       </tr>
//                     </thead>
//                     <tbody>
//                       {data.cases.slice(0, 10).map((c) => (
//                         <tr key={c.case_id}>
//                           <Td>
//                             <Link to={`/cases/${c.case_id}`} className="font-mono text-xs text-brand-600 hover:underline dark:text-brand-400">
//                               {c.case_id.slice(0, 8)}
//                             </Link>
//                           </Td>
//                           <Td><Mono>{c.reason_code}</Mono></Td>
//                           <Td><Badge>{c.reg_regime}</Badge></Td>
//                           <Td><Badge className={STATE_STYLE[c.state]}>{c.state}</Badge></Td>
//                           <Td className="text-xs tabular-nums text-neutral-600 dark:text-neutral-400">
//                             {dateTime(c.resolve_deadline)}
//                           </Td>
//                           <Td>
//                             <span className={cx(
//                               "text-xs font-medium tabular-nums",
//                               c.days_remaining < 0 ? "text-red-700 dark:text-red-400"
//                                 : c.days_remaining <= 3 ? "text-amber-700 dark:text-amber-400"
//                                 : "text-neutral-600 dark:text-neutral-400",
//                             )}>
//                               {relativeDays(c.resolve_deadline)}
//                             </span>
//                           </Td>
//                         </tr>
//                       ))}
//                     </tbody>
//                   </Table>
//                 </TableWrap>
//               )}
//             </Async>
//           </CardBody>
//         </Card>
//       )}
//     </>
//   );
// }

// /** Portfolio counts, computed from the case list the API returned — these
//  *  are aggregations of real rows, not invented figures. */
// function PortfolioStats({ data }: { data: CaseListResponse }) {
//   const byState = new Map<string, number>();
//   const byReason = new Map<string, number>();
//   let totalMinor = 0;
//   let currency = "";

//   for (const c of data.cases) {
//     byState.set(c.state, (byState.get(c.state) ?? 0) + 1);
//     byReason.set(c.reason_code, (byReason.get(c.reason_code) ?? 0) + 1);
//     totalMinor += c.amount_minor;
//     currency ||= c.currency;
//   }

//   const escalated = byState.get("ESCALATED") ?? 0;
//   const adjudicated = byState.get("ADJUDICATED") ?? 0;
//   const settled = byState.get("SETTLED") ?? 0;
//   const resolved = adjudicated + settled;

//   if (data.total === 0) {
//     return (
//       <EmptyState
//         title="No cases yet"
//         description="File a dispute, or seed the demo ledger with scripts/seed_demo.py."
//         action={<Link to="/file"><Badge className="bg-brand-100 text-brand-800 dark:bg-brand-950 dark:text-brand-300">File a dispute →</Badge></Link>}
//       />
//     );
//   }

//   return (
//     <>
//       <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
//         <Stat label="Total cases" value={data.total} />
//         <Stat
//           label="Resolved"
//           value={resolved}
//           tone={resolved > 0 ? "good" : undefined}
//           hint={`${adjudicated} adjudicated · ${settled} settled`}
//         />
//         <Stat
//           label="Escalated"
//           value={escalated}
//           tone={escalated > 0 ? "warn" : undefined}
//           hint="awaiting a human"
//         />
//         <Stat
//           label="Disputed value"
//           value={currency ? money(totalMinor, currency) : "—"}
//           hint={`across ${data.cases.length} loaded`}
//         />
//       </dl>

//       <div className="mt-5 flex flex-wrap gap-1.5 border-t border-neutral-200 pt-4 dark:border-neutral-800">
//         {[...byReason.entries()].sort().map(([code, count]) => (
//           <Link key={code} to={`/cases?reason_code=${code}`}>
//             <Badge className="hover:bg-neutral-200 dark:hover:bg-neutral-700">
//               {code} <span className="tabular-nums opacity-70">{count}</span>
//             </Badge>
//           </Link>
//         ))}
//         {/* Case STATE, not outcome. This used to look the state up in
//             OUTCOME_LABEL — a category error that could only ever miss, since
//             no state name is an outcome name, so it fell through to the raw
//             token on every single badge while implying a translation was
//             happening. */}
//         {[...byState.entries()].sort().map(([state, count]) => (
//           <Link key={state} to={`/cases?state=${state}`}>
//             <Badge className={cx(STATE_STYLE[state], "hover:opacity-80")}>
//               {humanize(state)} <span className="tabular-nums opacity-70">{count}</span>
//             </Badge>
//           </Link>
//         ))}
//       </div>
//     </>
//   );
// }

import { Link } from "react-router-dom";
import {
  Async, Badge, Card, CardBody, CardHeader, EmptyState, Mono, Stat,
  Table, TableWrap, Td, Th, PageHeader
} from "@/components/ui";
import { api } from "@/lib/api";
import { STATE_STYLE, cx, dateTime, humanize, money, relativeDays } from "@/lib/format";
import { getSession, isPrivileged } from "@/lib/session";
import type { AtRiskResponse, CaseListResponse, ReadyResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/**
 * Overview.
 *
 * Every number here is a count the API returned. Nothing is estimated,
 * sampled, or hardcoded — where a figure cannot be obtained from the
 * backend it is simply not shown, rather than filled with a placeholder.
 */
export default function OverviewPage() {
  const session = getSession();
  const privileged = isPrivileged(session);

  const cases = useApi<CaseListResponse>(() => api.listCases({ limit: 200 }), []);
  const ready = useApi<ReadyResponse>(() => api.ready(), []);
  const atRisk = useApi<AtRiskResponse>(() => api.casesAtRisk(14), [], { enabled: privileged });

  return (
    <>
      <PageHeader
        eyebrow={
          session?.role === "CARD_MEMBER" ? "Card member portal"
          : session?.role === "MERCHANT" ? "Merchant console"
          : "Analyst workbench"
        }
        title="Overview"
        description="Disputes are adjudicated by a deterministic rule engine that emits a proof tree. When it cannot decide, it abstains and a human decides — abstention is a first-class output, never a default outcome."
      />

      <div className="grid gap-6 lg:grid-cols-3 animate-in fade-in slide-in-from-bottom-4 duration-700 delay-150 fill-mode-both">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Case portfolio"
            subtitle={
              privileged
                ? "Every case in the system."
                : "Scoped to you by the API — a party token cannot widen its own scope."
            }
          />
          <CardBody>
            <Async state={cases} label="cases">
              {(data) => <PortfolioStats data={data} />}
            </Async>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Adjudication readiness"
            subtitle="Per reason code. An under-calibrated code escalates every case."
          />
          <CardBody>
            <Async state={ready} label="service readiness">
              {(data) => (
                <div className="space-y-1">
                  {Object.entries(data.calibration).length === 0 && (
                    <div className="rounded-lg border border-dashed border-border p-4 text-center">
                      <p className="text-xs text-muted-foreground">No rulepacks loaded.</p>
                    </div>
                  )}
                  {Object.entries(data.calibration).map(([code, info]) => (
                    <div key={code} className="group flex items-center justify-between gap-3 rounded-lg border border-transparent p-2.5 transition-colors hover:border-border/50 hover:bg-secondary/30">
                      <span className="font-mono text-sm font-bold tracking-wider text-primary group-hover:text-primary/80 transition-colors">
                        {code}
                      </span>
                      <div className="flex items-center gap-3">
                        <span className="text-[11px] font-medium tabular-nums text-muted-foreground group-hover:text-foreground transition-colors">
                          n={info.n}
                        </span>
                        <Badge className={info.calibrated
                          ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                          : "border-amber-500/20 bg-amber-500/10 text-amber-600 dark:text-amber-400"}>
                          {info.calibrated ? "Calibrated" : "Escalates all"}
                        </Badge>
                      </div>
                    </div>
                  ))}
                  {data.note && (
                    <div className="mt-4 rounded-lg border border-amber-500/20 bg-amber-500/10 p-3">
                      <p className="text-[11px] font-medium leading-relaxed text-amber-700 dark:text-amber-400">
                        {data.note}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </Async>
          </CardBody>
        </Card>
      </div>

      {privileged && (
        <div className="mt-6 animate-in fade-in slide-in-from-bottom-4 duration-700 delay-300 fill-mode-both">
          <Card>
            <CardHeader
              title="Regulatory clock — cases at risk"
              subtitle="Open cases whose statutory resolution deadline falls within 14 days. Reg Z: 30-day acknowledgment, 90-day resolution. Reg E: 10 business days."
              actions={
                <Link to="/operations">
                  <Badge className="cursor-pointer transition-colors hover:bg-primary hover:text-primary-foreground">
                    Operations <span>→</span>
                  </Badge>
                </Link>
              }
            />
            <CardBody className="p-0 sm:p-0">
              <Async
                state={atRisk}
                label="cases at risk"
                isEmpty={(d) => d.cases.length === 0}
                empty={
                  <div className="p-8">
                    <EmptyState title="No cases at risk" description="No case is within 14 days of its resolution deadline." />
                  </div>
                }
              >
                {(data) => (
                  <TableWrap>
                    <Table ariaLabel="Cases approaching a regulatory deadline">
                      <thead>
                        <tr>
                          <Th>Case</Th><Th>Reason</Th><Th>Regime</Th>
                          <Th>State</Th><Th>Resolve by</Th><Th className="text-right">Remaining</Th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.cases.slice(0, 10).map((c) => (
                          <tr key={c.case_id} className="group">
                            <Td>
                              <Link to={`/cases/${c.case_id}`} className="font-mono text-xs font-bold text-primary transition-all hover:text-primary/70 hover:underline">
                                {c.case_id.slice(0, 8)}
                              </Link>
                            </Td>
                            <Td><Mono>{c.reason_code}</Mono></Td>
                            <Td><Badge className="bg-secondary/50 text-secondary-foreground">{c.reg_regime}</Badge></Td>
                            <Td><Badge className={cx(STATE_STYLE[c.state], "border-transparent")}>{c.state}</Badge></Td>
                            <Td className="text-[11px] font-medium tabular-nums text-muted-foreground group-hover:text-foreground transition-colors">
                              {dateTime(c.resolve_deadline)}
                            </Td>
                            <Td className="text-right">
                              <span className={cx(
                                "inline-flex rounded-full px-2.5 py-1 text-[11px] font-bold tabular-nums tracking-wide",
                                c.days_remaining < 0 ? "bg-destructive/10 text-destructive dark:text-red-400"
                                  : c.days_remaining <= 3 ? "bg-amber-500/10 text-amber-700 dark:text-amber-400"
                                  : "bg-secondary/50 text-muted-foreground",
                              )}>
                                {relativeDays(c.resolve_deadline)}
                              </span>
                            </Td>
                          </tr>
                        ))}
                      </tbody>
                    </Table>
                  </TableWrap>
                )}
              </Async>
            </CardBody>
          </Card>
        </div>
      )}
    </>
  );
}

/** Portfolio counts, computed from the case list the API returned — these
 *  are aggregations of real rows, not invented figures. */
function PortfolioStats({ data }: { data: CaseListResponse }) {
  const byState = new Map<string, number>();
  const byReason = new Map<string, number>();
  let totalMinor = 0;
  let currency = "";

  for (const c of data.cases) {
    byState.set(c.state, (byState.get(c.state) ?? 0) + 1);
    byReason.set(c.reason_code, (byReason.get(c.reason_code) ?? 0) + 1);
    totalMinor += c.amount_minor;
    currency ||= c.currency;
  }

  const escalated = byState.get("ESCALATED") ?? 0;
  const adjudicated = byState.get("ADJUDICATED") ?? 0;
  const settled = byState.get("SETTLED") ?? 0;
  const resolved = adjudicated + settled;

  if (data.total === 0) {
    return (
      <EmptyState
        title="No cases yet"
        description="File a dispute, or seed the demo ledger with scripts/seed_demo.py."
        action={
          <Link to="/file">
            <Badge className="cursor-pointer bg-primary/10 text-primary transition-colors hover:bg-primary hover:text-primary-foreground border-primary/20">
              File a dispute <span>→</span>
            </Badge>
          </Link>
        }
      />
    );
  }

  return (
    <>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-8 sm:grid-cols-4">
        <Stat label="Total cases" value={data.total} />
        <Stat
          label="Resolved"
          value={resolved}
          tone={resolved > 0 ? "good" : undefined}
          hint={`${adjudicated} adjudicated · ${settled} settled`}
        />
        <Stat
          label="Escalated"
          value={escalated}
          tone={escalated > 0 ? "warn" : undefined}
          hint="Awaiting human review"
        />
        <Stat
          label="Disputed value"
          value={currency ? money(totalMinor, currency) : "—"}
          hint={`Across ${data.cases.length} loaded`}
        />
      </dl>

      <div className="mt-8 flex flex-col gap-6 border-t border-border/50 pt-6 lg:flex-row lg:items-start lg:justify-between">
        
        <div className="flex-1 space-y-3">
          <h4 className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Volume by Reason Code</h4>
          <div className="flex flex-wrap gap-2">
            {[...byReason.entries()].sort().map(([code, count]) => (
              <Link key={code} to={`/cases?reason_code=${code}`}>
                <Badge className="cursor-pointer bg-secondary/40 text-foreground transition-all hover:-translate-y-0.5 hover:bg-primary hover:text-primary-foreground hover:shadow-md border-transparent">
                  {code}
                  <span className="ml-1.5 flex items-center justify-center rounded-md bg-background/50 px-1.5 py-0.5 text-[9px] tabular-nums text-inherit opacity-80 mix-blend-luminosity">
                    {count}
                  </span>
                </Badge>
              </Link>
            ))}
          </div>
        </div>

        <div className="flex-1 space-y-3">
          <h4 className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Volume by Status</h4>
          <div className="flex flex-wrap gap-2">
            {[...byState.entries()].sort().map(([state, count]) => (
              <Link key={state} to={`/cases?state=${state}`}>
                <Badge className={cx(STATE_STYLE[state], "cursor-pointer transition-all hover:-translate-y-0.5 hover:opacity-80 hover:shadow-md border-transparent")}>
                  {humanize(state)}
                  <span className="ml-1.5 flex items-center justify-center rounded-md bg-background/30 px-1.5 py-0.5 text-[9px] tabular-nums text-inherit opacity-90 mix-blend-luminosity">
                    {count}
                  </span>
                </Badge>
              </Link>
            ))}
          </div>
        </div>

      </div>
    </>
  );
}