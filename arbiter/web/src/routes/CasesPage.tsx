import { Link, useSearchParams } from "react-router-dom";
import {
  Async, Badge, Button, Card, CardBody, EmptyState, Mono, PageHeader,
  Select, Table, TableWrap, Td, Th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { STATE_STYLE, date, money, relativeDays } from "@/lib/format";
import type { CaseListResponse, CaseState } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { useRulepacks } from "@/lib/useRulepacks";

/**
 * Case list.
 *
 * Filters live in the URL so a reviewer can bookmark or share a working
 * queue. Scope is enforced server-side from the bearer token — there is no
 * query parameter a caller can set to widen it, which is why the filter
 * controls here only ever narrow.
 */

const PAGE_SIZE = 25;

const STATES: CaseState[] = [
  "INTAKE", "GATHERING", "AWAITING_EVIDENCE", "ANALYSING",
  "ADJUDICATED", "ESCALATED", "SETTLED", "DEFLECTED", "REOPENED",
];

export default function CasesPage() {
  const { rulepacks } = useRulepacks();
  const [params, setParams] = useSearchParams();
  const state = params.get("state") ?? "";
  const reasonCode = params.get("reason_code") ?? "";
  const offset = Number(params.get("offset") ?? 0);

  const cases = useApi<CaseListResponse>(
    () => api.listCases({
      state: state || undefined,
      reason_code: reasonCode || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
    [state, reasonCode, offset],
  );

  function update(next: Record<string, string>) {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value) merged.set(key, value);
      else merged.delete(key);
    }
    // Any filter change resets paging: page 3 of a different filter is
    // almost never what the user meant.
    if (!("offset" in next)) merged.delete("offset");
    setParams(merged);
  }

  return (
    <>
      <PageHeader
        title="Cases"
        description="Every dispute you are a party to. Reviewers and administrators see all of them."
        actions={<Link to="/file"><Button variant="primary">File a dispute</Button></Link>}
      />

      <Card>
        <CardBody className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-48">
              <label htmlFor="state-filter" className="mb-1 block text-xs font-medium text-neutral-700 dark:text-neutral-300">
                State
              </label>
              <Select
                id="state-filter"
                value={state}
                onChange={(v) => update({ state: v })}
                options={[{ value: "", label: "All states" },
                  ...STATES.map((s) => ({ value: s, label: s }))]}
              />
            </div>
            <div className="w-40">
              <label htmlFor="reason-filter" className="mb-1 block text-xs font-medium text-neutral-700 dark:text-neutral-300">
                Reason code
              </label>
              {/* Driven by the server's loaded rulepacks. A hardcoded list
                  here meant a case filed under a newly added reason code was
                  in the table and impossible to filter to. */}
              <Select
                id="reason-filter"
                value={reasonCode}
                onChange={(v) => update({ reason_code: v })}
                options={[
                  { value: "", label: "All codes" },
                  ...rulepacks.map((pack) => ({
                    value: pack.reason_code,
                    label: `${pack.reason_code} — ${pack.title}`,
                  })),
                ]}
              />
            </div>
            {(state || reasonCode) && (
              <Button variant="ghost" size="sm" onClick={() => setParams(new URLSearchParams())}>
                Clear filters
              </Button>
            )}
          </div>

          <Async
            state={cases}
            label="cases"
            isEmpty={(d) => d.cases.length === 0}
            empty={
              <EmptyState
                title="No cases match"
                description={state || reasonCode
                  ? "Try clearing the filters."
                  : "Nothing has been filed against this account yet."}
              />
            }
          >
            {(data) => (
              <>
                <TableWrap>
                  <Table ariaLabel="Dispute cases">
                    <thead>
                      <tr>
                        <Th>Case</Th><Th>Reason</Th><Th>Amount</Th><Th>State</Th>
                        <Th>Filed</Th><Th>Resolve by</Th><Th>Merchant</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.cases.map((c) => (
                        <tr key={c.case_id} className="transition-colors hover:bg-neutral-50 dark:hover:bg-neutral-800/50">
                          <Td>
                            <Link
                              to={`/cases/${c.case_id}`}
                              className="font-mono text-xs font-medium text-brand-600 hover:underline dark:text-brand-400"
                            >
                              {c.case_id.slice(0, 8)}
                            </Link>
                          </Td>
                          <Td><Mono>{c.reason_code}</Mono></Td>
                          <Td className="whitespace-nowrap text-xs font-medium tabular-nums text-neutral-800 dark:text-neutral-200">
                            {money(c.amount_minor, c.currency)}
                          </Td>
                          <Td><Badge className={STATE_STYLE[c.state]}>{c.state}</Badge></Td>
                          <Td className="whitespace-nowrap text-xs text-neutral-600 dark:text-neutral-400">
                            {date(c.filed_at)}
                          </Td>
                          <Td className="whitespace-nowrap text-xs text-neutral-600 dark:text-neutral-400">
                            {relativeDays(c.resolve_deadline)}
                          </Td>
                          <Td>
                            {c.merchant_responded ? (
                              <Badge className="bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                                responded
                              </Badge>
                            ) : (
                              <Badge
                                className="bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300"
                                title="ARBITER does not treat merchant silence as concession — the case is adjudicated on the merits from Amex-held data."
                              >
                                silent
                              </Badge>
                            )}
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </TableWrap>

                <div className="flex items-center justify-between gap-3 pt-1">
                  <p className="text-xs text-neutral-500 dark:text-neutral-400">
                    Showing{" "}
                    <span className="tabular-nums">
                      {data.offset + 1}–{Math.min(data.offset + data.cases.length, data.total)}
                    </span>{" "}
                    of <span className="tabular-nums">{data.total}</span>
                  </p>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      disabled={data.offset === 0}
                      onClick={() => update({ offset: String(Math.max(0, data.offset - PAGE_SIZE)) })}
                    >
                      Previous
                    </Button>
                    <Button
                      size="sm"
                      disabled={data.offset + data.cases.length >= data.total}
                      onClick={() => update({ offset: String(data.offset + PAGE_SIZE) })}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              </>
            )}
          </Async>
        </CardBody>
      </Card>
    </>
  );
}
